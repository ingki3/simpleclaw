"""Verified projection을 한 번만 호출하는 persona-aware final draft composer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable

from pydantic import JsonValue

from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.llm.models import LLMRequest, LLMResponse
from simpleclaw.persona.models import CompositionPersonaProjection

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
_MAX_LIMITATION_PATHS = 64
_SAMPLING_POLICY_VERSION = "request_temperature_v1"
_RENDER_PLAN_POLICY_VERSION = "typed_path_segments_v1"
_URL_RE = re.compile(r"https?://[^\s)>\]}]+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_SAFE_PUNCTUATION_RE = re.compile(r"^[\s.,!?;:'\"()\[\]{}\-–—·/+]*$")
_SAFE_CONNECTOR_WORDS = frozenset(
    {
        "각각",
        "이며",
        "이고",
        "입니다",
        "됩니다",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "와",
        "과",
        "도",
        "로",
        "에",
        "불확실",
        "확인되지",
        "확인할",
        "알",
        "수",
        "없습니다",
        "제한",
        "추정",
        "불확실합니다",
        "the",
        "a",
        "an",
        "and",
        "is",
        "are",
        "was",
        "were",
        "with",
        "respectively",
        "unknown",
        "uncertain",
        "unverified",
        "could",
        "not",
        "verify",
        "cannot",
    }
)
_CONNECTOR_TEXT = {
    "space": " ",
    "comma_space": ", ",
    "middle_dot_space": " · ",
    "semicolon_space": "; ",
    "period": ".",
    "question_mark": "?",
    "topic_eun_space": "은 ",
    "topic_neun_space": "는 ",
    "subject_i_space": "이 ",
    "subject_ga_space": "가 ",
    "object_eul_space": "을 ",
    "object_reul_space": "를 ",
    "and_wa_space": "와 ",
    "and_gwa_space": "과 ",
    "also_do_space": "도 ",
    "to_ro_space": "로 ",
    "at_e_space": "에 ",
    "copula_imyeo_space": "이며 ",
    "copula_igo_space": "이고 ",
    "polite_copula_period": "입니다.",
    "polite_become_period": "됩니다.",
    "english_and_space": " and ",
    "english_is_space": " is ",
    "english_are_space": " are ",
    "english_with_space": " with ",
    "english_respectively_period": " respectively.",
    "limitation_uncertain_period": " uncertain.",
    "limitation_unverified_period": " unverified.",
    "limitation_korean_uncertain_period": " 불확실합니다.",
}
_LIMITATION_CONNECTORS = frozenset(
    {
        "limitation_uncertain_period",
        "limitation_unverified_period",
        "limitation_korean_uncertain_period",
    }
)


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


def _list_root(path: str) -> str | None:
    """첫 concrete index 앞 root를 추출해 auxiliary list 혼합을 막는다."""
    match = re.search(r"\[\d+\]", path)
    return None if match is None else path[: match.start()]


def _validate_connector(
    connector: str,
    *,
    concrete: dict[str, JsonValue],
) -> None:
    """Connector가 새 literal·관계·진단을 운반하지 못하게 한다."""
    if _URL_RE.search(connector) or _NUMBER_RE.search(connector):
        raise FinalResponseComposerError("render plan connector contains a literal")
    for token in _WORD_RE.findall(connector):
        if token.casefold() not in _SAFE_CONNECTOR_WORDS:
            raise FinalResponseComposerError(
                "render plan connector contains a content word"
            )
    symbols = _WORD_RE.sub("", connector)
    if not _SAFE_PUNCTUATION_RE.fullmatch(symbols):
        raise FinalResponseComposerError(
            "render plan connector contains unsafe symbols"
        )
    for projected in concrete.values():
        pattern = projected_scalar_literal_pattern(projected)
        if pattern is not None and pattern.search(connector) is not None:
            raise FinalResponseComposerError(
                "render plan connector copied a projected literal"
            )


def _citable_paths(
    value: CompositionInputV1,
) -> tuple[dict[str, JsonValue], tuple[str, ...], tuple[str, ...]]:
    """현재 projection과 active relation에서 provider가 선택 가능한 path를 만든다."""
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
    allowed = relation.evidence_paths if relation is not None else available
    if not allowed:
        raise FinalResponseComposerError("composer input has no citable scalar paths")
    if len(allowed) > _MAX_CITATION_PATHS:
        raise FinalResponseComposerError("composer input has too many citable paths")
    if any(path not in available for path in allowed):
        raise FinalResponseComposerError(
            "structural evidence contains an unsafe scalar path"
        )
    limitations = tuple(
        f"unresolved_claims[{index}]" for index in range(len(value.unresolved_claims))
    )
    if len(limitations) > _MAX_LIMITATION_PATHS:
        raise FinalResponseComposerError("composer input has too many limitation paths")
    return concrete, tuple(allowed), limitations


def _response_schema(value: CompositionInputV1) -> dict:
    """현재 projection의 path와 bounded connector만 허용하는 schema를 만든다."""
    _, cited_paths, limitation_paths = _citable_paths(value)
    schema = CompositionRenderPlanV1.model_json_schema(by_alias=True)
    properties = schema["properties"]
    segment_schema = schema.get("$defs", {}).get("CompositionRenderSegmentV1")
    if not isinstance(segment_schema, dict):
        raise FinalResponseComposerError("render plan schema is incomplete")
    segment_schema["properties"]["path"]["enum"] = list(cited_paths)
    if value.structural_evidence_relations:
        properties["segments"]["minItems"] = len(cited_paths)
        properties["segments"]["maxItems"] = len(cited_paths)
    if limitation_paths:
        properties["limitation_paths"]["items"]["enum"] = list(limitation_paths)
    else:
        properties["limitation_paths"]["maxItems"] = 0
    return schema


def materialize_render_plan(
    value: CompositionInputV1,
    plan: CompositionRenderPlanV1,
    *,
    max_output_chars: int = 3_500,
) -> DraftResponseV1:
    """검증된 plan 순서대로 projected literal을 중앙에서 exactly-once 삽입한다."""
    concrete, allowed_paths, limitation_paths = _citable_paths(value)
    paths = tuple(segment.path for segment in plan.segments)
    if len(paths) != len(set(paths)):
        raise FinalResponseComposerError("render plan contains duplicate paths")
    if any("[*]" in path or path not in concrete for path in paths):
        raise FinalResponseComposerError("render plan contains an invalid path")
    if value.structural_evidence_relations:
        if paths != allowed_paths:
            raise FinalResponseComposerError(
                "render plan does not match structural evidence order"
            )
    else:
        order = {path: index for index, path in enumerate(allowed_paths)}
        if any(path not in order for path in paths) or [
            order[path] for path in paths
        ] != sorted(order[path] for path in paths):
            raise FinalResponseComposerError("render plan paths are not canonical")
    list_roots = {root for path in paths if (root := _list_root(path)) is not None}
    if len(list_roots) > 1:
        raise FinalResponseComposerError("render plan mixes list roots")
    if list_roots and (
        value.composition_list_root is None
        or list_roots != {value.composition_list_root}
    ):
        raise FinalResponseComposerError("render plan uses an auxiliary list root")
    if tuple(plan.limitation_paths) != limitation_paths:
        raise FinalResponseComposerError("render plan limitation paths are incomplete")
    limitation_segments = tuple(
        index
        for index, segment in enumerate(plan.segments)
        if segment.connector in _LIMITATION_CONNECTORS
    )
    if limitation_paths:
        if limitation_segments != (len(plan.segments) - 1,):
            raise FinalResponseComposerError(
                "render plan requires one final limitation ending"
            )
    elif limitation_segments:
        raise FinalResponseComposerError(
            "render plan has an ungrounded limitation ending"
        )

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
    for segment, projected in zip(plan.segments, selected_values, strict=True):
        literal = _scalar_literal(projected)
        prior = rendered_by_literal.get(literal)
        if prior is not None and not _same_json_scalar(prior, projected):
            raise FinalResponseComposerError(
                "render plan has a typed literal collision"
            )
        rendered_by_literal[literal] = projected
        connector = _CONNECTOR_TEXT.get(segment.connector)
        if connector is None:
            raise FinalResponseComposerError("render plan connector is unknown")
        _validate_connector(connector, concrete=concrete)
        chunks.extend((literal, connector))
    content = "".join(chunks)
    if content != content.strip() or len(content) > max_output_chars:
        raise FinalResponseComposerError("materialized content has an invalid length")
    return DraftResponseV1(
        content=content,
        cited_paths=paths,
        limitation_paths=plan.limitation_paths,
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
