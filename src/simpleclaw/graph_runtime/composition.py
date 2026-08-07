"""Final composition, response guard, durable write-once 경계를 구현한다."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from .composition_journal import FinalArtifactJournal, FinalArtifactInvariantError
from .contracts import DraftArtifactV1, FinalArtifactV1, NormalizedAssetResultV1
from .idempotency import (
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from .status import AssetResultStatus, EffectStatus, TerminalOutcome

ComposeCallback = Callable[[object], object | Awaitable[object]]
GuardCallback = Callable[..., object | Awaitable[object]]
SafeRenderCallback = Callable[..., str]


async def _await_if_needed(value):
    if inspect.isawaitable(value):
        return await value
    return value


class FinalCompositionRuntime:
    """Composer/guard 실패를 재실행 없이 generic fallback 또는 억제로 수렴시킨다."""

    def __init__(
        self,
        *,
        compose: ComposeCallback,
        guard: GuardCallback,
        safe_render: SafeRenderCallback,
        journal: FinalArtifactJournal | None = None,
        composer_fingerprint: str = "asset_text_compat_v1",
    ) -> None:
        if not composer_fingerprint.strip():
            raise ValueError("composer_fingerprint is required")
        self._compose = compose
        self._guard = guard
        self._safe_render = safe_render
        self._journal = journal
        self._composer_fingerprint = composer_fingerprint
        self._finals: dict[str, tuple[str, FinalArtifactV1]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, request_id: str) -> asyncio.Lock:
        """같은 process의 concurrent replay에서 provider 호출을 한 번으로 묶는다."""
        if self._journal is not None:
            shared_lock = getattr(self._journal, "lock_for", None)
            if callable(shared_lock):
                return shared_lock(request_id)
        return self._locks.setdefault(request_id, asyncio.Lock())

    async def finalize(
        self,
        *,
        request_id: str,
        normalized_result: NormalizedAssetResultV1,
        outcome: TerminalOutcome,
        composition_input: object | None = None,
    ) -> FinalArtifactV1 | None:
        """Accepted final을 durable하게 기록한 뒤에만 delivery 경계로 반환한다."""
        if composition_input is not None:
            if getattr(composition_input, "request_id", None) != request_id:
                raise FinalArtifactInvariantError("composition request_id mismatch")
            if (
                getattr(composition_input, "normalized_payload_hash", None)
                != normalized_result.payload_hash
            ):
                raise FinalArtifactInvariantError("composition payload hash mismatch")
            if (
                getattr(composition_input, "result_status", None)
                is not AssetResultStatus.RESOLVED
            ) or (
                getattr(composition_input, "effect_status", None)
                not in {EffectStatus.NONE, EffectStatus.VERIFIED}
            ):
                return None

        payload_hash = normalized_result.payload_hash
        async with self._lock_for(request_id):
            if self._journal is not None:
                existing = await self._journal.load(
                    request_id=request_id,
                    normalized_payload_hash=payload_hash,
                    composer_fingerprint=self._composer_fingerprint,
                )
                if existing is not None:
                    self._finals[request_id] = (payload_hash, existing)
                    return existing
            cached = self._finals.get(request_id)
            if cached is not None:
                if cached[0] != payload_hash:
                    raise FinalArtifactInvariantError(
                        "final artifact request reused with different normalized payload"
                    )
                return cached[1]

            content: str | None = None
            draft: Any | None = None
            try:
                argument: object = composition_input or normalized_result
                candidate = await _await_if_needed(self._compose(argument))
                if composition_input is None:
                    if isinstance(candidate, str) and candidate.strip():
                        content = candidate.strip()
                elif (
                    getattr(candidate, "schema_version", None)
                    == "draft_response.v1"
                    and isinstance(getattr(candidate, "content", None), str)
                ):
                    draft = candidate
            except Exception as exc:  # noqa: BLE001 - deterministic fallback 대상
                if getattr(exc, "stop_condition", None) in {
                    "deadline",
                    "budget_exhausted",
                }:
                    raise
                content = None
                draft = None

            guarded = False
            if composition_input is None and content is not None:
                try:
                    guarded = bool(await _await_if_needed(self._guard(content)))
                except Exception:  # noqa: BLE001 - guard 실패는 fail-closed
                    guarded = False
            elif composition_input is not None and draft is not None:
                try:
                    result = await _await_if_needed(
                        self._guard(composition_input, draft)
                    )
                    guarded = (
                        getattr(result, "accepted", None) is True
                    )
                    if guarded:
                        content = draft.content.strip()
                except Exception:  # noqa: BLE001 - guard 실패는 fail-closed
                    guarded = False

            if not guarded:
                try:
                    safe_content = (
                        self._safe_render(normalized_result)
                        if composition_input is None
                        else self._safe_render()
                    )
                except Exception:  # noqa: BLE001 - fallback 부재 시 delivery 억제
                    return None
                if not isinstance(safe_content, str) or not safe_content.strip():
                    return None
                content = safe_content.strip()
            assert content is not None

            artifact_id = canonical_artifact_id(request_id, content)
            draft_artifact = DraftArtifactV1(
                artifact_id=artifact_id,
                request_id=request_id,
                content=content,
                outcome=outcome,
            )
            final = FinalArtifactV1(
                artifact_id=draft_artifact.artifact_id,
                request_id=draft_artifact.request_id,
                content=draft_artifact.content,
                outcome=draft_artifact.outcome,
                content_hash=canonical_artifact_content_hash(draft_artifact.content),
            )
            if self._journal is not None:
                final = await self._journal.record_or_reuse(
                    request_id=request_id,
                    normalized_payload_hash=payload_hash,
                    composer_fingerprint=self._composer_fingerprint,
                    artifact=final,
                )
            self._finals[request_id] = (payload_hash, final)
            return final
