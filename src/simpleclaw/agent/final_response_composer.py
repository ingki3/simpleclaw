"""Verified projection을 한 번만 호출하는 persona-aware final draft composer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.llm.models import LLMRequest, LLMResponse
from simpleclaw.persona.models import CompositionPersonaProjection

from .composition_citations import (
    CITATION_CANONICALIZATION_POLICY_VERSION,
    canonicalize_draft_citations,
)
from .composition_contracts import CompositionInputV1, DraftResponseV1
from .composition_projection import flatten_public_facts

ComposerSend = Callable[[LLMRequest], Awaitable[LLMResponse]]
_MAX_CITATION_PATHS = 128
_MAX_LIMITATION_PATHS = 64
_SAMPLING_POLICY_VERSION = "request_temperature_v1"


class FinalResponseComposerError(RuntimeError):
    """Provider 원문을 노출하지 않고 중앙 composer 실패를 표시한다."""


def _response_schema(value: CompositionInputV1) -> dict:
    """현재 projection의 concrete scalar path만 허용하는 schema를 만든다."""
    concrete = flatten_public_facts(value.public_facts)
    cited_paths = [
        path
        for path, projected in concrete.items()
        if not isinstance(projected, dict | list)
    ]
    limitation_paths = [
        f"unresolved_claims[{index}]"
        for index in range(len(value.unresolved_claims))
    ]
    if not cited_paths:
        raise FinalResponseComposerError("composer input has no citable scalar paths")
    if len(cited_paths) > _MAX_CITATION_PATHS:
        raise FinalResponseComposerError("composer input has too many citable paths")
    if len(limitation_paths) > _MAX_LIMITATION_PATHS:
        raise FinalResponseComposerError("composer input has too many limitation paths")

    schema = DraftResponseV1.model_json_schema(by_alias=True)
    properties = schema["properties"]
    properties["cited_paths"]["items"]["enum"] = cited_paths
    if limitation_paths:
        properties["limitation_paths"]["items"]["enum"] = limitation_paths
    else:
        properties["limitation_paths"]["maxItems"] = 0
    return schema


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
                "citation_canonicalization": (
                    CITATION_CANONICALIZATION_POLICY_VERSION
                ),
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
                "sampling_policy": _SAMPLING_POLICY_VERSION,
                "temperature": self._temperature,
                "version": "central_persona_v1",
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
            draft = DraftResponseV1.model_validate_json(response.text)
            if len(draft.content) > self._max_output_chars:
                raise FinalResponseComposerError("composer output exceeded configured cap")
            return canonicalize_draft_citations(value, draft)
        except FinalResponseComposerError:
            raise
        except Exception as exc:
            raise FinalResponseComposerError("composer response was invalid") from exc
