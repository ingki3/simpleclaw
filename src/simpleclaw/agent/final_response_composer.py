"""Verified projection을 한 번만 호출하는 persona-aware final draft composer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.llm.models import LLMRequest, LLMResponse

from .composition_contracts import CompositionInputV1, DraftResponseV1

ComposerSend = Callable[[LLMRequest], Awaitable[LLMResponse]]


class FinalResponseComposerError(RuntimeError):
    """Provider 원문을 노출하지 않고 중앙 composer 실패를 표시한다."""


class FinalResponseComposer:
    """현재 persona와 verified facts만으로 tool 없는 draft 한 개를 만든다."""

    def __init__(
        self,
        *,
        send: ComposerSend,
        persona_prompt: str,
        max_tokens: int,
        backend_name: str,
        max_output_chars: int = 3_500,
    ) -> None:
        if not backend_name.strip():
            raise ValueError("composer backend_name is required")
        if max_tokens <= 0:
            raise ValueError("composer max_tokens must be positive")
        self._send = send
        self._persona_prompt = persona_prompt.strip()
        self._max_tokens = max_tokens
        self._backend_name = backend_name.strip()
        self._max_output_chars = max(256, min(int(max_output_chars), 3_500))
        self._prompt = load_system_prompt(
            "langgraph_v4_composer", refresh=True
        ).system_prompt

    @property
    def fingerprint(self) -> str:
        """Replay continuity에 사용할 deterministic composer policy hash다."""
        payload = json.dumps(
            {
                "backend": self._backend_name,
                "max_tokens": self._max_tokens,
                "max_output_chars": self._max_output_chars,
                "persona": self._persona_prompt,
                "prompt": self._prompt,
                "version": "central_persona_v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def compose(self, value: CompositionInputV1) -> DraftResponseV1:
        """Repair·retry·asset 호출 없이 provider를 정확히 한 번 호출한다."""
        system_prompt = "\n\n---\n\n".join(
            part for part in (self._persona_prompt, self._prompt) if part
        )
        request = LLMRequest(
            system_prompt=system_prompt,
            user_message=value.model_dump_json(by_alias=True),
            backend_name=self._backend_name,
            tools=None,
            max_tokens=self._max_tokens,
            response_mime_type="application/json",
            response_schema=DraftResponseV1.model_json_schema(by_alias=True),
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
            return draft
        except FinalResponseComposerError:
            raise
        except Exception as exc:
            raise FinalResponseComposerError("composer response was invalid") from exc
