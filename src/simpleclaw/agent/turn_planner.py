"""UnifiedTurnPlan을 생성하는 단일 structured LLM planner service.

bounded context 후보와 compact capability catalog를 한 요청에 조립하고, provider
schema가 강제한 JSON을 ``UnifiedTurnPlan``으로 검증한다. 문법 실패는 deterministic
truncated-tail repair 후 route retry 한 번만 허용하며, 모두 실패하면 의미 기반
keyword fallback 없이 ``PlannerUnavailable``로 fail-closed한다.

BIZ-497부터 production orchestrator의 shadow/canary/primary 경로에 연결된다.
sampled canary와 primary의 planner 장애는 legacy semantic fallback 없이
fail-closed하며, off mode가 deterministic rollback을 제공한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.planner_catalog import PlannerCatalog
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.agent.turn_plan import (
    UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
    UnifiedTurnPlan,
    decode_turn_plan_payload,
    parse_turn_plan_data,
)
from simpleclaw.llm.models import LLMRequest
from simpleclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_PLANNER_EXAMPLES_PROMPT = "unified_turn_planner_examples"
_PROMPT_SECTION_SEPARATOR = "\n\n---\n\n"

_REPAIR_REQUIRED_FIELDS = (
    "context",
    "clarification",
    "fact_check",
    "execution",
    "confidence",
)


class PlannerUnavailable(RuntimeError):
    """structured 계획을 안전하게 확정하지 못했음을 나타내는 fail-closed 오류."""

    def __init__(
        self,
        message: str,
        *,
        boundary_code: str | None = None,
    ) -> None:
        """원문 없이 최종 boundary 실패의 안정적 코드만 선택적으로 보존한다."""
        self.boundary_code = boundary_code
        super().__init__(message)


class PlanBoundaryViolation(ValueError):
    """LLM 계획이 실제 context/catalog 실행 경계를 벗어났음을 나타낸다."""

    def __init__(self, code: str) -> None:
        """raw ID/name을 노출하지 않는 안정적 오류 코드만 보존한다."""
        self.code = code
        super().__init__(f"turn_plan_boundary.{code}")


def _raw_selected_turn_ids(
    raw_data: Mapping[str, object] | None,
    *,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    """clamp 전 selected ID를 strict 문자열 tuple로 읽는다."""
    if raw_data is None:
        return fallback
    context = raw_data.get("context")
    if not isinstance(context, Mapping):
        return fallback
    value = context.get("selected_turn_ids")
    if not isinstance(value, list):
        raise PlanBoundaryViolation("invalid_selected_turn_ids")
    selected: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PlanBoundaryViolation("invalid_selected_turn_ids")
        selected.append(item.strip())
    return tuple(selected)


def _raw_asset_identity(
    value: object,
    *,
    allow_none: bool,
) -> tuple[str, str] | None:
    """clamp 전 asset object를 catalog identity로 변환한다."""
    if value in (None, "__none__"):
        if allow_none:
            return None
        raise PlanBoundaryViolation("invalid_allowed_asset")
    if not isinstance(value, Mapping):
        raise PlanBoundaryViolation("invalid_asset_reference")
    asset_type = value.get("asset_type")
    asset_name = value.get("asset_name", value.get("name"))
    if asset_type == "none" and asset_name == "__none__":
        if allow_none:
            return None
        raise PlanBoundaryViolation("invalid_allowed_asset")
    if (
        asset_type not in {"native_tool", "skill", "recipe"}
        or not isinstance(asset_name, str)
        or not asset_name.strip()
    ):
        raise PlanBoundaryViolation("invalid_asset_reference")
    return str(asset_type), asset_name.strip()


def _requested_asset_scope(
    plan: UnifiedTurnPlan,
    raw_data: Mapping[str, object] | None,
) -> tuple[
    tuple[str, str] | None,
    tuple[tuple[str, str], ...],
    tuple[str, ...],
    bool,
]:
    """raw 응답의 asset scope와 legacy execution-only 여부를 추출한다."""
    if raw_data is None or not isinstance(raw_data.get("execution"), Mapping):
        primary = plan.capability.primary_asset
        primary_identity = (
            None
            if primary is None
            else (primary.asset_type, primary.name)
        )
        allowed = tuple(
            (asset.asset_type, asset.name)
            for asset in plan.capability.supporting_assets
        )
        return primary_identity, allowed, plan.execution.allowed_tools, False

    execution = raw_data["execution"]
    capability = raw_data.get("capability")
    if isinstance(capability, Mapping):
        primary_identity = _raw_asset_identity(
            capability.get("primary_asset"),
            allow_none=True,
        )
        raw_allowed = capability.get("supporting_assets")
        legacy_execution_scope = False
    else:
        primary_identity = _raw_asset_identity(
            execution.get("primary_asset"),
            allow_none=True,
        )
        raw_allowed = execution.get("allowed_assets")
        legacy_execution_scope = True
    if not isinstance(raw_allowed, list):
        raise PlanBoundaryViolation("invalid_allowed_assets")
    allowed: list[tuple[str, str]] = []
    for item in raw_allowed:
        identity = _raw_asset_identity(item, allow_none=False)
        if identity is not None:
            allowed.append(identity)

    raw_tools = execution.get("allowed_tools")
    if not isinstance(raw_tools, list):
        raise PlanBoundaryViolation("invalid_allowed_tools")
    tools: list[str] = []
    for item in raw_tools:
        if not isinstance(item, str) or not item.strip():
            raise PlanBoundaryViolation("invalid_allowed_tool")
        tools.append(item.strip())
    return (
        primary_identity,
        tuple(allowed),
        tuple(tools),
        legacy_execution_scope,
    )


def _normalize_redundant_exact_recipe_delegate(
    raw_data: Mapping[str, object],
    *,
    catalog: PlannerCatalog,
) -> Mapping[str, object]:
    """안전한 exact recipe가 소유한 중복 top-level delegate만 제거한다.

    provider가 recipe 내부 구현 세부인 ``execute_skill``을 top-level scope에도
    복제하는 경우가 있다. capability-native full recipe, 빈 supporting scope,
    단일 ``execute_skill``이라는 닫힌 형태이고 catalog의 typed/read-only 계약까지
    일치할 때만 실행 범위를 빈 allowlist로 축소한다. 그 밖의 tool/asset은 원문을
    보존해 기존 boundary/PlanGate가 fail-closed하도록 한다.
    """
    capability = raw_data.get("capability")
    execution = raw_data.get("execution")
    if not isinstance(capability, Mapping) or not isinstance(execution, Mapping):
        return raw_data
    if capability.get("coverage") != "full_coverage":
        return raw_data
    try:
        primary = _raw_asset_identity(
            capability.get("primary_asset"),
            allow_none=True,
        )
    except PlanBoundaryViolation:
        return raw_data
    if primary is None or primary[0] != "recipe":
        return raw_data
    if capability.get("supporting_assets") != []:
        return raw_data
    if execution.get("allowed_tools") != ["execute_skill"]:
        return raw_data

    catalog_asset = next(
        (
            asset
            for asset in catalog.assets
            if asset.runtime_visible
            and (asset.asset_type, asset.name) == primary
        ),
        None,
    )
    if (
        catalog_asset is None
        or not catalog_asset.declared
        or catalog_asset.coverage != "full_coverage"
        or catalog_asset.input_contract != "query.v1"
        or catalog_asset.output_contract != "asset_result.v1"
        or not catalog_asset.read_only
        or catalog_asset.side_effects
        or catalog_asset.requires_confirmation
    ):
        return raw_data

    normalized = dict(raw_data)
    normalized_execution = dict(execution)
    normalized_execution["allowed_tools"] = []
    normalized["execution"] = normalized_execution
    return normalized


def validate_turn_plan_boundaries(
    plan: UnifiedTurnPlan,
    *,
    candidates: ContextCandidateSet,
    catalog: PlannerCatalog,
    raw_data: Mapping[str, object] | None = None,
) -> None:
    """LLM 출력이 현재 candidate/runtime catalog 경계를 넓히지 못하게 한다.

    validation은 prompt 지시가 아니라 실행 전 trust boundary다. raw structured
    payload를 우선 사용하므로 topic-shift/clarify clamp가 제거한 hallucinated
    ID나 asset도 조용히 성공 처리되지 않는다.
    """
    candidate_ids = {
        candidate.turn_id for candidate in candidates.candidates
    }
    selected_ids = _raw_selected_turn_ids(
        raw_data,
        fallback=plan.context.selected_turn_ids,
    )
    if not set(selected_ids).issubset(candidate_ids):
        raise PlanBoundaryViolation("unknown_selected_turn_id")

    runtime_assets = {
        (asset.asset_type, asset.name): asset
        for asset in catalog.assets
        if asset.runtime_visible
    }
    runtime_tools = {
        asset.name: asset
        for asset in runtime_assets.values()
        if asset.asset_type == "native_tool"
    }
    primary, allowed_assets, allowed_tools, legacy_execution_scope = (
        _requested_asset_scope(
            plan,
            raw_data,
        )
    )
    if (
        legacy_execution_scope
        and primary is not None
        and primary not in allowed_assets
    ):
        raise PlanBoundaryViolation("primary_not_allowed")

    referenced_assets = set(allowed_assets)
    if primary is not None:
        referenced_assets.add(primary)
    if any(identity not in runtime_assets for identity in referenced_assets):
        raise PlanBoundaryViolation("unknown_or_internal_asset")
    if any(tool_name not in runtime_tools for tool_name in allowed_tools):
        raise PlanBoundaryViolation("unknown_or_internal_tool")

    confirmation_assets = [
        *(runtime_assets[identity] for identity in referenced_assets),
        *(runtime_tools[tool_name] for tool_name in allowed_tools),
    ]
    confirmation_required = any(
        asset.side_effects or asset.requires_confirmation
        for asset in confirmation_assets
    )
    if confirmation_required and not plan.execution.requires_confirmation:
        raise PlanBoundaryViolation("confirmation_required")


def build_turn_planner_user_prompt(
    *,
    text: str,
    candidates: ContextCandidateSet,
    catalog: PlannerCatalog,
) -> str:
    """현재 turn·ID 문맥 후보·runtime catalog를 deterministic JSON으로 조립한다."""
    payload = {
        "current_user_message": text or "",
        "context_candidates": json.loads(candidates.to_prompt_json()),
        "context_candidates_truncated": candidates.truncated,
        "capability_catalog": json.loads(catalog.to_prompt_json()),
        "catalog_fingerprint": catalog.fingerprint,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _repair_truncated_json_object(text: str) -> dict[str, object] | None:
    """마지막 완결 값까지만 보존해 잘린 JSON object tail을 결정적으로 닫는다."""
    if not text or text[0] != "{":
        return None

    stack: list[str] = []
    candidates: list[tuple[int, tuple[str, ...]]] = []
    in_string = False
    escape = False
    string_is_value = False
    expect_value = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
                if string_is_value:
                    candidates.append((index + 1, tuple(stack)))
                    expect_value = False
            index += 1
            continue
        if char == '"':
            in_string = True
            string_is_value = expect_value or bool(stack and stack[-1] == "[")
            index += 1
            continue
        if char == ":":
            expect_value = True
            index += 1
            continue
        if char == ",":
            expect_value = bool(stack and stack[-1] == "[")
            index += 1
            continue
        if char in "{[":
            stack.append(char)
            expect_value = char == "["
            index += 1
            continue
        if char in "}]":
            if not stack:
                return None
            stack.pop()
            candidates.append((index + 1, tuple(stack)))
            expect_value = False
            index += 1
            continue
        if char.isspace():
            index += 1
            continue

        token_end = index
        while token_end < len(text) and text[token_end] not in ",]}" and (
            not text[token_end].isspace()
        ):
            token_end += 1
        if token_end < len(text):
            try:
                json.loads(text[index:token_end])
            except ValueError:
                pass
            else:
                candidates.append((token_end, tuple(stack)))
        expect_value = False
        index = token_end

    for cut, open_containers in reversed(candidates):
        closers = "".join(
            "}" if container == "{" else "]"
            for container in reversed(open_containers)
        )
        try:
            repaired = json.loads(text[:cut] + closers)
        except ValueError:
            continue
        if isinstance(repaired, dict):
            return repaired
    return None


def _strip_json_fence(text: str) -> str:
    """repair 전에 markdown JSON fence를 제거한다."""
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def repair_turn_plan_payload(
    payload: str,
    *,
    original_text: str,
    catalog_fingerprint: str = "",
) -> UnifiedTurnPlan | None:
    """설명 tail만 잘린 payload에서 완결된 핵심 계획을 보존한다."""
    data = _repair_turn_plan_data(payload)
    if data is None:
        return None
    return parse_turn_plan_data(
        data,
        original_text=original_text,
        catalog_fingerprint=catalog_fingerprint,
    )


def _repair_turn_plan_data(
    payload: str,
) -> Mapping[str, object] | None:
    """boundary 검증에도 재사용할 수 있도록 복구된 raw object를 반환한다."""
    data = _repair_truncated_json_object(_strip_json_fence(payload))
    if not isinstance(data, Mapping):
        return None
    if any(field_name not in data for field_name in _REPAIR_REQUIRED_FIELDS):
        return None
    return data


async def plan_turn_with_llm(
    text: str,
    *,
    candidates: ContextCandidateSet,
    catalog: PlannerCatalog,
    router,
    max_tokens: int = 2048,
    reasoning: dict | None = None,
    examples_prompt_name: str = _PLANNER_EXAMPLES_PROMPT,
) -> UnifiedTurnPlan:
    """한 structured 요청으로 plan을 만들고 repair→retry 후 fail-closed한다.

    Args:
        text: 현재 사용자 원문. 계획의 ``original_text``로 그대로 보존한다.
        candidates: 안정적 ID와 trust가 포함된 bounded 문맥 후보.
        catalog: runtime-visible capability snapshot과 fingerprint.
        router: ``LLMRouter`` 또는 동일 ``send`` 계약을 가진 테스트 대역.
        max_tokens: structured 응답 출력 상한.
        reasoning: 켜진 경우에만 전달하는 provider-neutral reasoning 힌트.

    Returns:
        parser와 semantic clamp를 통과한 ``UnifiedTurnPlan``.

    Raises:
        PlannerUnavailable: parse, deterministic repair, route retry가 모두 실패.
    """
    original = text or ""
    try:
        base_prompt = load_system_prompt("unified_turn_planner")
        examples_prompt = load_system_prompt(examples_prompt_name)
        logger.info(
            "Unified planner prompts loaded: base=%s@%d examples=%s@%d",
            base_prompt.name,
            base_prompt.version,
            examples_prompt.name,
            examples_prompt.version,
        )
        request = LLMRequest(
            system_prompt=(
                base_prompt.system_prompt
                + _PROMPT_SECTION_SEPARATOR
                + examples_prompt.field("template")
            ),
            user_message=build_turn_planner_user_prompt(
                text=original,
                candidates=candidates,
                catalog=catalog,
            ),
            route_name="turn_analysis",
            max_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
            require_structured_output=True,
            usage_task="turn_planner",
        )
    except Exception as exc:  # noqa: BLE001 — raw 원문 없이 service 오류로 정규화.
        logger.warning(
            "Unified turn planner input assembly failed "
            "(error_type=%s route=turn_analysis)",
            type(exc).__name__,
        )
        raise PlannerUnavailable("unified turn planner unavailable") from None
    if isinstance(reasoning, dict) and reasoning.get("enabled"):
        request.reasoning = dict(reasoning)

    diagnostic: dict[str, object] = {
        "error_type": None,
        "raw_len": 0,
        "finish_reason": None,
        "boundary_code": None,
    }

    def _validate_response(response) -> UnifiedTurnPlan:
        """provider 응답을 parse하고 같은 호출 안에서 repair까지 시도한다."""
        response_text = getattr(response, "text", "") or ""
        diagnostic["raw_len"] = len(response_text)
        diagnostic["finish_reason"] = getattr(response, "finish_reason", None)
        diagnostic["boundary_code"] = None

        def _build_validated(
            data: Mapping[str, object],
        ) -> UnifiedTurnPlan:
            """raw object를 모델로 조립한 뒤 실제 runtime 경계와 대조한다."""
            data = _normalize_redundant_exact_recipe_delegate(
                data,
                catalog=catalog,
            )
            plan = parse_turn_plan_data(
                data,
                original_text=original,
                catalog_fingerprint=catalog.fingerprint,
            )
            try:
                validate_turn_plan_boundaries(
                    plan,
                    candidates=candidates,
                    catalog=catalog,
                    raw_data=data,
                )
            except PlanBoundaryViolation as exc:
                diagnostic["error_type"] = type(exc).__name__
                diagnostic["boundary_code"] = exc.code
                raise
            return plan

        try:
            return _build_validated(decode_turn_plan_payload(response_text))
        except PlanBoundaryViolation:
            # 의미 경계 위반은 tail repair로 고칠 수 없고 route retry가 담당한다.
            raise
        except (TypeError, ValueError) as exc:
            diagnostic["error_type"] = type(exc).__name__
            repaired_data = _repair_turn_plan_data(response_text)
            if repaired_data is not None:
                repaired = _build_validated(repaired_data)
                logger.warning(
                    "Unified turn planner repair preserved the plan "
                    "(error_type=%s raw_len=%d finish_reason=%s "
                    "route=turn_analysis repair_status=repaired)",
                    diagnostic["error_type"],
                    diagnostic["raw_len"],
                    diagnostic["finish_reason"],
                )
                return repaired
            raise

    try:
        if isinstance(router, LLMRouter):
            return await router.send_validated(request, _validate_response)
        return _validate_response(await router.send(request))
    except Exception as exc:  # noqa: BLE001 — raw 없이 명시적 fail-closed로 변환.
        diagnostic["error_type"] = type(exc).__name__
        logger.warning(
            "Unified turn planner unavailable after route retry "
            "(error_type=%s raw_len=%d finish_reason=%s "
            "route=turn_analysis repair_status=failed boundary_code=%s)",
            diagnostic["error_type"],
            diagnostic["raw_len"],
            diagnostic["finish_reason"],
            diagnostic["boundary_code"],
        )
        boundary_code = (
            exc.code
            if isinstance(exc, PlanBoundaryViolation)
            else None
        )
        raise PlannerUnavailable(
            "unified turn planner unavailable",
            boundary_code=boundary_code,
        ) from None
