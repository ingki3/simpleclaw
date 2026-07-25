"""UnifiedTurnPlan을 생성하는 단일 structured LLM planner service.

bounded context 후보와 compact capability catalog를 한 요청에 조립하고, provider
schema가 강제한 JSON을 ``UnifiedTurnPlan``으로 검증한다. 문법 실패는 deterministic
truncated-tail repair 후 route retry 한 번만 허용하며, 모두 실패하면 의미 기반
keyword fallback 없이 ``PlannerUnavailable``로 fail-closed한다.

현재 production orchestrator에는 연결하지 않는다. 후속 shadow/primary 전환 이슈가
이 service를 주입하고 장애 시 응답 정책을 결정한다.
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
    parse_turn_plan_data,
    parse_turn_plan_payload,
)
from simpleclaw.llm.models import LLMRequest
from simpleclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_REPAIR_REQUIRED_FIELDS = (
    "context",
    "clarification",
    "fact_check",
    "execution",
    "confidence",
)


class PlannerUnavailable(RuntimeError):
    """structured 계획을 안전하게 확정하지 못했음을 나타내는 fail-closed 오류."""


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
    data = _repair_truncated_json_object(_strip_json_fence(payload))
    if not isinstance(data, Mapping):
        return None
    if any(field_name not in data for field_name in _REPAIR_REQUIRED_FIELDS):
        return None
    return parse_turn_plan_data(
        data,
        original_text=original_text,
        catalog_fingerprint=catalog_fingerprint,
    )


async def plan_turn_with_llm(
    text: str,
    *,
    candidates: ContextCandidateSet,
    catalog: PlannerCatalog,
    router,
    max_tokens: int = 2048,
    reasoning: dict | None = None,
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
        request = LLMRequest(
            system_prompt=load_system_prompt(
                "unified_turn_planner"
            ).system_prompt,
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
    }

    def _validate_response(response) -> UnifiedTurnPlan:
        """provider 응답을 parse하고 같은 호출 안에서 repair까지 시도한다."""
        response_text = getattr(response, "text", "") or ""
        diagnostic["raw_len"] = len(response_text)
        diagnostic["finish_reason"] = getattr(response, "finish_reason", None)
        try:
            return parse_turn_plan_payload(
                response_text,
                original_text=original,
                catalog_fingerprint=catalog.fingerprint,
            )
        except (TypeError, ValueError) as exc:
            diagnostic["error_type"] = type(exc).__name__
            repaired = repair_turn_plan_payload(
                response_text,
                original_text=original,
                catalog_fingerprint=catalog.fingerprint,
            )
            if repaired is not None:
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
            "route=turn_analysis repair_status=failed)",
            diagnostic["error_type"],
            diagnostic["raw_len"],
            diagnostic["finish_reason"],
        )
        raise PlannerUnavailable("unified turn planner unavailable") from None
