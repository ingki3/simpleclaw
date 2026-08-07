"""BIZ-628 — accepted final의 durable write-once/replay 회귀."""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    DraftResponseV1,
)
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.graph_runtime.composition import FinalCompositionRuntime
from simpleclaw.graph_runtime.composition_journal import (
    FinalArtifactInvariantError,
    SQLiteFinalArtifactJournal,
)
from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    ContractRefV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    EffectStatus,
    TerminalOutcome,
)


def _values(request_id: str = "request-1"):
    facts = {"data": {"items": [{"rank": 1, "team": "KT", "wins": 59}]}}
    value = CompositionInputV1(
        request_id=request_id,
        question="KBO 1위 팀을 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts=facts,
    )
    result = NormalizedAssetResultV1(
        invocation_id="invocation",
        output_contract=ContractRefV1(
            contract_id="recipe.sports-live.output",
            version="1",
            owner_ref=value.asset_ref,
            schema_hash="schema-hash",
        ),
        status=AssetResultStatus.RESOLVED,
        payload={"side_effect": False, **facts},
        payload_hash="payload-hash",
        effect_status=EffectStatus.NONE,
    )
    return value, result


def _draft() -> DraftResponseV1:
    return DraftResponseV1(
        content="KBO 1위는 KT이며 59승입니다.",
        cited_paths=("data.items[0].team", "data.items[0].wins"),
    )


@pytest.mark.asyncio
async def test_restart_reuses_first_accepted_final_without_recomposing(tmp_path) -> None:
    value, result = _values()
    compose = AsyncMock(return_value=_draft())
    journal = SQLiteFinalArtifactJournal(tmp_path / "invocations.sqlite3")
    runtime = FinalCompositionRuntime(
        compose=compose,
        guard=guard_final_response,
        safe_render=lambda: "generic fallback",
        journal=journal,
        composer_fingerprint="composer-v1",
    )

    finals = await asyncio.gather(
        *(
            runtime.finalize(
                request_id=value.request_id,
                normalized_result=result,
                outcome=TerminalOutcome.COMPLETED,
                composition_input=value,
            )
            for _ in range(32)
        )
    )
    replay_compose = AsyncMock(return_value=_draft())
    replay = await FinalCompositionRuntime(
        compose=replay_compose,
        guard=guard_final_response,
        safe_render=lambda: "generic fallback",
        journal=SQLiteFinalArtifactJournal(tmp_path / "invocations.sqlite3"),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    assert compose.await_count == 1
    assert replay_compose.await_count == 0
    assert replay == finals[0]
    assert all(item == finals[0] for item in finals)


@pytest.mark.asyncio
async def test_concurrent_runtime_instances_compose_exactly_once(tmp_path) -> None:
    value, result = _values("shared-runtime-request")
    compose = AsyncMock(return_value=_draft())
    db_path = tmp_path / "invocations.sqlite3"

    def runtime() -> FinalCompositionRuntime:
        return FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=lambda: "generic fallback",
            journal=SQLiteFinalArtifactJournal(db_path),
            composer_fingerprint="composer-v1",
        )

    finals = await asyncio.gather(
        *(
            runtime().finalize(
                request_id=value.request_id,
                normalized_result=result,
                outcome=TerminalOutcome.COMPLETED,
                composition_input=value,
            )
            for _ in range(32)
        )
    )

    assert compose.await_count == 1
    assert all(final == finals[0] for final in finals)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_same_request_with_different_payload_hash_conflicts(tmp_path) -> None:
    value, result = _values()
    journal = SQLiteFinalArtifactJournal(tmp_path / "invocations.sqlite3")
    runtime = FinalCompositionRuntime(
        compose=AsyncMock(return_value=_draft()),
        guard=guard_final_response,
        safe_render=lambda: "generic fallback",
        journal=journal,
        composer_fingerprint="composer-v1",
    )
    await runtime.finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )
    changed_result = result.model_copy(update={"payload_hash": "changed"})
    changed_input = value.model_copy(
        update={"normalized_payload_hash": "changed"}
    )

    with pytest.raises(FinalArtifactInvariantError, match="different normalized"):
        await runtime.finalize(
            request_id=value.request_id,
            normalized_result=changed_result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=changed_input,
        )


@pytest.mark.asyncio
async def test_sqlite_write_lock_does_not_block_event_loop(tmp_path) -> None:
    value, result = _values("locked-request")
    db_path = tmp_path / "invocations.sqlite3"
    journal = SQLiteFinalArtifactJournal(db_path)
    assert await journal.load(
        request_id=value.request_id,
        normalized_payload_hash=value.normalized_payload_hash,
        composer_fingerprint="composer-v1",
    ) is None
    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    runtime = FinalCompositionRuntime(
        compose=AsyncMock(return_value=_draft()),
        guard=guard_final_response,
        safe_render=lambda: "generic fallback",
        journal=journal,
        composer_fingerprint="composer-v1",
    )
    task = asyncio.create_task(
        runtime.finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )
    )
    ticks = 0
    try:
        for _ in range(8):
            await asyncio.sleep(0.01)
            ticks += 1
        assert task.done() is False
    finally:
        blocker.commit()
        blocker.close()

    assert await task is not None
    assert ticks == 8
