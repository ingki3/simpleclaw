"""Final composition과 response guard의 write-once 경계를 구현한다."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from .contracts import DraftArtifactV1, FinalArtifactV1, NormalizedAssetResultV1
from .idempotency import (
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from .status import TerminalOutcome

ComposeCallback = Callable[[NormalizedAssetResultV1], str | Awaitable[str]]
GuardCallback = Callable[[str], bool | Awaitable[bool]]
SafeRenderCallback = Callable[[NormalizedAssetResultV1], str]


async def _await_if_needed(value):
    if inspect.isawaitable(value):
        return await value
    return value


class FinalCompositionRuntime:
    """composer 실패/guard 거부를 solver 재실행 없이 안전 응답으로 수렴시킨다."""

    def __init__(
        self,
        *,
        compose: ComposeCallback,
        guard: GuardCallback,
        safe_render: SafeRenderCallback,
    ) -> None:
        self._compose = compose
        self._guard = guard
        self._safe_render = safe_render
        self._finals: dict[str, FinalArtifactV1] = {}

    async def finalize(
        self,
        *,
        request_id: str,
        normalized_result: NormalizedAssetResultV1,
        outcome: TerminalOutcome,
    ) -> FinalArtifactV1 | None:
        """guard 통과 전에는 final을 만들지 않고 safe renderer를 최대 한 번 호출한다."""
        existing = self._finals.get(request_id)
        if existing is not None:
            return existing

        content: str | None = None
        try:
            candidate = await _await_if_needed(self._compose(normalized_result))
            if isinstance(candidate, str) and candidate.strip():
                content = candidate.strip()
        except Exception:  # noqa: BLE001 - composer 실패는 deterministic fallback 대상
            content = None

        guarded = False
        if content is not None:
            try:
                guarded = bool(await _await_if_needed(self._guard(content)))
            except Exception:  # noqa: BLE001 - guard 실패는 fail-closed 거부
                guarded = False

        if not guarded:
            # pre-validated deterministic renderer다. 비동기/tool callback을 허용하지
            # 않아 이 fallback이 새 dispatch 경로가 되지 않게 한다.
            try:
                safe_content = self._safe_render(normalized_result)
            except Exception:  # noqa: BLE001 - safe renderer 부재로 delivery를 억제
                return None
            if not isinstance(safe_content, str) or not safe_content.strip():
                return None
            content = safe_content.strip()

        artifact_id = canonical_artifact_id(request_id, content)
        draft = DraftArtifactV1(
            artifact_id=artifact_id,
            request_id=request_id,
            content=content,
            outcome=outcome,
        )
        final = FinalArtifactV1(
            artifact_id=draft.artifact_id,
            request_id=draft.request_id,
            content=draft.content,
            outcome=draft.outcome,
            content_hash=canonical_artifact_content_hash(draft.content),
        )
        prior = self._finals.setdefault(request_id, final)
        if prior != final:
            raise ValueError("final artifact is write-once")
        return prior
