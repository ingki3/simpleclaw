"""Verified projection을 한 번만 호출하는 persona-aware final draft composer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from pydantic import JsonValue

from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.llm.models import LLMRequest, LLMResponse
from simpleclaw.persona.models import CompositionPersonaProjection

from .composition_admissibility import citation_list_root_violation
from .composition_citations import (
    CITATION_CANONICALIZATION_POLICY_VERSION,
    projected_scalar_literal_pattern,
)
from .composition_contracts import (
    CompositionInputV1,
    CompositionRenderPlanV1,
    DraftResponseV1,
)
from .composition_projection import flatten_public_facts

ComposerSend = Callable[[LLMRequest], Awaitable[LLMResponse]]
_MAX_CITATION_PATHS = 128
_SAMPLING_POLICY_VERSION = "request_temperature_v1"
_RENDER_PLAN_POLICY_VERSION = "source_order_structural_punctuation_v3"
_STRUCTURAL_SEPARATOR_TEXT = {
    "space": " ",
    "comma_space": ", ",
    "middle_dot_space": " · ",
    "semicolon_space": "; ",
}


class FinalResponseComposerError(RuntimeError):
    """Provider 원문을 노출하지 않고 중앙 composer 실패를 표시한다."""


def _scalar_literal(value: JsonValue) -> str:
    """JSON scalar 타입과 원문 whitespace를 바꾸지 않는 literal을 만든다."""
    if value is None or isinstance(value, dict | list):
        raise FinalResponseComposerError("render plan selected an unsafe scalar")
    if isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, str):
        if not value or value != value.strip():
            raise FinalResponseComposerError(
                "render plan selected a non-canonical string"
            )
        return value
    rendered = str(value)
    if not rendered or rendered != rendered.strip():
        raise FinalResponseComposerError("render plan selected an unsafe scalar")
    return rendered


def _same_json_scalar(left: JsonValue, right: JsonValue) -> bool:
    """bool/number equality가 서로 다른 JSON 타입을 합치지 않게 한다."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return left == right
    return type(left) is type(right) and left == right


def _citable_paths(
    value: CompositionInputV1,
) -> tuple[dict[str, JsonValue], tuple[str, ...]]:
    """Source contract가 소유하는 exact fact path set/order를 반환한다."""
    concrete = flatten_public_facts(value.public_facts)
    available = tuple(
        path
        for path, projected in concrete.items()
        if not isinstance(projected, dict | list)
        and projected is not None
        and projected_scalar_literal_pattern(projected) is not None
    )
    relation = (
        value.structural_evidence_relations[0]
        if value.structural_evidence_relations
        else None
    )
    if relation is not None:
        allowed = relation.evidence_paths
    else:
        allowed = tuple(value.citable_paths)
        if not allowed or any(path not in available for path in allowed):
            raise FinalResponseComposerError(
                "composer input has no verified citable-path contract"
            )
    if not allowed:
        raise FinalResponseComposerError("composer input has no citable scalar paths")
    if len(allowed) > _MAX_CITATION_PATHS:
        raise FinalResponseComposerError("composer input has too many citable paths")
    if any(path not in available for path in allowed):
        raise FinalResponseComposerError(
            "structural evidence contains an unsafe scalar path"
        )
    if value.unresolved_claims:
        raise FinalResponseComposerError(
            "unresolved claims require a fact-free fallback"
        )
    return concrete, tuple(allowed)


def _response_schema(value: CompositionInputV1) -> dict:
    """Fact/path authority를 노출하지 않는 domain-neutral plan schema다."""
    _citable_paths(value)
    return CompositionRenderPlanV1.model_json_schema(by_alias=True)


def materialize_render_plan(
    value: CompositionInputV1,
    plan: CompositionRenderPlanV1,
    *,
    max_output_chars: int = 3_500,
) -> DraftResponseV1:
    """Source-owned canonical path order로 projected literal을 exactly-once 삽입한다."""
    concrete, paths = _citable_paths(value)
    list_root_violation = citation_list_root_violation(
        paths,
        declared_root=value.composition_list_root,
    )
    if list_root_violation == "mixed_list_roots":
        raise FinalResponseComposerError("render plan mixes list roots")
    if list_root_violation == "auxiliary_list_root":
        raise FinalResponseComposerError("render plan uses an auxiliary list root")
    selected_values = tuple(concrete[path] for path in paths)
    bool_numbers = {int(item) for item in selected_values if isinstance(item, bool)}
    numbers = {
        item
        for item in selected_values
        if isinstance(item, int | float) and not isinstance(item, bool)
    }
    if any(number in bool_numbers for number in numbers):
        raise FinalResponseComposerError("render plan has a bool-number collision")
    rendered_by_literal: dict[str, JsonValue] = {}
    chunks: list[str] = []
    for projected in selected_values:
        literal = _scalar_literal(projected)
        prior = rendered_by_literal.get(literal)
        if prior is not None and not _same_json_scalar(prior, projected):
            raise FinalResponseComposerError(
                "render plan has a typed literal collision"
            )
        rendered_by_literal[literal] = projected
        chunks.append(literal)
    separator = _STRUCTURAL_SEPARATOR_TEXT[plan.separator]
    content = separator.join(chunks) + "."
    if content != content.strip() or len(content) > max_output_chars:
        raise FinalResponseComposerError("materialized content has an invalid length")
    return DraftResponseV1(
        content=content,
        cited_paths=paths,
        limitation_paths=(),
    )


class FinalResponseComposer:
    """현재 persona와 verified facts만으로 tool 없는 draft 한 개를 만든다."""

    def __init__(
        self,
        *,
        send: ComposerSend,
        persona_projection: CompositionPersonaProjection,
        max_tokens: int,
        backend_name: str,
        max_output_chars: int = 3_500,
        temperature: float = 0.0,
    ) -> None:
        if not backend_name.strip():
            raise ValueError("composer backend_name is required")
        if max_tokens <= 0:
            raise ValueError("composer max_tokens must be positive")
        if not 0.0 <= temperature <= 1.0:
            raise ValueError("composer temperature must be between 0.0 and 1.0")
        self._send = send
        self._persona_projection = persona_projection
        self._max_tokens = max_tokens
        self._backend_name = backend_name.strip()
        self._max_output_chars = max(256, min(int(max_output_chars), 3_500))
        self._temperature = float(temperature)
        self._prompt = load_system_prompt(
            "langgraph_v4_composer", refresh=True
        ).system_prompt

    @property
    def fingerprint(self) -> str:
        """Replay continuity에 사용할 deterministic composer policy hash다."""
        payload = json.dumps(
            {
                "backend": self._backend_name,
                "citation_canonicalization": (CITATION_CANONICALIZATION_POLICY_VERSION),
                "max_tokens": self._max_tokens,
                "max_output_chars": self._max_output_chars,
                "persona_content_hash": hashlib.sha256(
                    self._persona_projection.instruction_text.encode("utf-8")
                ).hexdigest(),
                "persona_policy_version": self._persona_projection.policy_version,
                "persona_projection_fingerprint": (
                    self._persona_projection.fingerprint
                ),
                "prompt": self._prompt,
                "render_plan": _RENDER_PLAN_POLICY_VERSION,
                "sampling_policy": _SAMPLING_POLICY_VERSION,
                "temperature": self._temperature,
                "version": "central_render_plan_v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def temperature(self) -> float:
        """검증된 request-scoped sampling 값을 노출한다."""
        return self._temperature

    async def compose(self, value: CompositionInputV1) -> DraftResponseV1:
        """Repair·retry·asset 호출 없이 provider를 정확히 한 번 호출한다."""
        system_prompt = "\n\n---\n\n".join(
            part
            for part in (self._persona_projection.instruction_text, self._prompt)
            if part
        )
        request = LLMRequest(
            system_prompt=system_prompt,
            user_message=value.model_dump_json(by_alias=True),
            backend_name=self._backend_name,
            tools=None,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            response_mime_type="application/json",
            response_schema=_response_schema(value),
            require_structured_output=True,
            usage_task="langgraph_v4_composer",
        )
        try:
            response = await self._send(request)
            if response.tool_calls:
                raise FinalResponseComposerError("composer returned tool calls")
            if not isinstance(response.text, str) or not response.text.strip():
                raise FinalResponseComposerError("composer returned an empty response")
            plan = CompositionRenderPlanV1.model_validate_json(response.text)
            return materialize_render_plan(
                value,
                plan,
                max_output_chars=self._max_output_chars,
            )
        except FinalResponseComposerError:
            raise
        except Exception as exc:
            raise FinalResponseComposerError("composer response was invalid") from exc
