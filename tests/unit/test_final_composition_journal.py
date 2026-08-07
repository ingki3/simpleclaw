"""BIZ-628 — accepted final의 durable write-once/replay 회귀."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from unittest.mock import AsyncMock, Mock

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
    facts = {
        "data": {
            "category": "KBO",
            "items": [{"rank": 1, "team": "KT", "wins": 59}],
        }
    }
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
        cited_paths=(
            "data.category",
            "data.items[0].team",
            "data.items[0].wins",
        ),
    )


class _ComposerStop(RuntimeError):
    def __init__(self, stop_condition: str) -> None:
        self.stop_condition = stop_condition
        super().__init__(
            f"provider raw diagnostic: {stop_condition}; KBO 1위 KT 59승"
        )


async def _assert_stop_records_generic_fallback_and_replay_reuses_it(
    tmp_path,
    *,
    stop_condition: str,
    request_id: str,
) -> None:
    value, result = _values(request_id)
    db_path = tmp_path / f"{stop_condition}.sqlite3"
    compose = AsyncMock(side_effect=_ComposerStop(stop_condition))
    safe_render = Mock(
        return_value="요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    first = await FinalCompositionRuntime(
        compose=compose,
        guard=guard_final_response,
        safe_render=safe_render,
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    replay_compose = AsyncMock(return_value=_draft())
    replay = await FinalCompositionRuntime(
        compose=replay_compose,
        guard=guard_final_response,
        safe_render=Mock(return_value="replay must not render"),
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    assert first is not None
    assert replay == first
    assert compose.await_count == 1
    assert safe_render.call_count == 1
    assert replay_compose.await_count == 0
    assert all(
        forbidden not in first.content
        for forbidden in (
            "provider",
            "raw diagnostic",
            stop_condition,
            "KBO",
            "KT",
            "1위",
            "59",
        )
    )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (request_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_budget_exhaustion_records_generic_fallback_and_replay_reuses_it(
    tmp_path,
) -> None:
    await _assert_stop_records_generic_fallback_and_replay_reuses_it(
        tmp_path,
        stop_condition="budget_exhausted",
        request_id="budget-exhaustion-request",
    )


@pytest.mark.asyncio
async def test_deadline_signal_records_generic_fallback_and_replay_reuses_it(
    tmp_path,
) -> None:
    await _assert_stop_records_generic_fallback_and_replay_reuses_it(
        tmp_path,
        stop_condition="deadline",
        request_id="deadline-request",
    )


@pytest.mark.asyncio
async def test_actual_asyncio_timeout_records_generic_fallback_and_replay_reuses_it(
    tmp_path,
) -> None:
    value, result = _values("actual-deadline-request")
    db_path = tmp_path / "actual-deadline.sqlite3"
    compose_started = asyncio.Event()
    deadline = asyncio.timeout(None)

    async def compose(_value):
        compose_started.set()
        deadline.reschedule(asyncio.get_running_loop().time())
        await asyncio.Future()

    safe_render = Mock(
        return_value="요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    runtime = FinalCompositionRuntime(
        compose=compose,
        guard=guard_final_response,
        safe_render=safe_render,
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
        controlled_deadline_expired=lambda: (
            deadline.expired()
        ),
    )

    async with deadline:
        first = await runtime.finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )
    current_task = asyncio.current_task()
    assert current_task is not None
    assert current_task.cancelling() == 0

    replay_compose = AsyncMock(return_value=_draft())
    replay = await FinalCompositionRuntime(
        compose=replay_compose,
        guard=guard_final_response,
        safe_render=Mock(return_value="replay must not render"),
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    assert compose_started.is_set()
    assert first is not None
    assert replay == first
    assert replay_compose.await_count == 0
    assert safe_render.call_count == 1
    assert all(
        forbidden not in first.content
        for forbidden in ("provider", "raw", "KBO", "KT", "1위", "59")
    )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_actual_guard_timeout_records_generic_fallback_and_replay_reuses_it(
    tmp_path,
) -> None:
    value, result = _values("guard-deadline-request")
    db_path = tmp_path / "guard-deadline.sqlite3"
    guard_started = asyncio.Event()
    deadline = asyncio.timeout(None)

    async def guard(_value, _draft):
        guard_started.set()
        deadline.reschedule(asyncio.get_running_loop().time())
        await asyncio.Future()

    safe_render = Mock(
        return_value="요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    runtime = FinalCompositionRuntime(
        compose=AsyncMock(return_value=_draft()),
        guard=guard,
        safe_render=safe_render,
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
        controlled_deadline_expired=deadline.expired,
    )

    async with deadline:
        first = await runtime.finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    replay_compose = AsyncMock(return_value=_draft())
    replay_guard = AsyncMock(return_value=Mock(accepted=True))
    replay = await FinalCompositionRuntime(
        compose=replay_compose,
        guard=replay_guard,
        safe_render=Mock(return_value="replay must not render"),
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    assert guard_started.is_set()
    assert first is not None
    assert replay == first
    assert replay_compose.await_count == 0
    assert replay_guard.await_count == 0
    assert safe_render.call_count == 1
    assert all(
        forbidden not in first.content
        for forbidden in ("provider", "raw", "KBO", "KT", "1위", "59")
    )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_actual_record_timeout_records_generic_fallback_and_replay_reuses_it(
    tmp_path,
) -> None:
    value, result = _values("record-deadline-request")
    db_path = tmp_path / "record-deadline.sqlite3"
    accepted_draft = DraftResponseV1(
        content="KBO · KT · 59",
        cited_paths=(
            "data.category",
            "data.items[0].team",
            "data.items[0].wins",
        ),
    )

    deadline = asyncio.timeout(None)

    class DelayedFirstRecordJournal(SQLiteFinalArtifactJournal):
        def __init__(self) -> None:
            super().__init__(db_path)
            self.record_calls = 0

        async def record_or_reuse(self, **kwargs):
            self.record_calls += 1
            if self.record_calls == 1:
                deadline.reschedule(asyncio.get_running_loop().time())
                await asyncio.Future()
            return await super().record_or_reuse(**kwargs)

    safe_render = Mock(
        return_value="요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    journal = DelayedFirstRecordJournal()
    runtime = FinalCompositionRuntime(
        compose=AsyncMock(return_value=accepted_draft),
        guard=guard_final_response,
        safe_render=safe_render,
        journal=journal,
        composer_fingerprint="composer-v1",
        controlled_deadline_expired=deadline.expired,
    )

    async with deadline:
        first = await runtime.finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    replay_compose = AsyncMock(return_value=_draft())
    replay_guard = AsyncMock(return_value=Mock(accepted=True))
    replay = await FinalCompositionRuntime(
        compose=replay_compose,
        guard=replay_guard,
        safe_render=Mock(return_value="replay must not render"),
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    assert first is not None
    assert replay == first
    assert replay_compose.await_count == 0
    assert replay_guard.await_count == 0
    assert journal.record_calls == 2
    assert safe_render.call_count == 1
    assert all(
        forbidden not in first.content
        for forbidden in ("provider", "raw", "KBO", "KT", "1위", "59")
    )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_claim_timeout_without_confirmed_ownership_fails_closed(
    tmp_path,
) -> None:
    value, result = _values("claim-deadline-request")
    db_path = tmp_path / "claim-deadline.sqlite3"
    deadline = asyncio.timeout(None)

    class DeadlineDuringClaimJournal(SQLiteFinalArtifactJournal):
        def __init__(self) -> None:
            super().__init__(db_path)
            self.claim_calls = 0

        async def claim_composition(self, **kwargs):
            self.claim_calls += 1
            deadline.reschedule(asyncio.get_running_loop().time())
            await asyncio.Future()

    compose = AsyncMock(return_value=_draft())
    safe_render = Mock(
        return_value="요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    journal = DeadlineDuringClaimJournal()
    runtime = FinalCompositionRuntime(
        compose=compose,
        guard=guard_final_response,
        safe_render=safe_render,
        journal=journal,
        composer_fingerprint="composer-v1",
        controlled_deadline_expired=deadline.expired,
    )

    async with deadline:
        first = await runtime.finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    replay_compose = AsyncMock(return_value=_draft())
    replay = await FinalCompositionRuntime(
        compose=replay_compose,
        guard=lambda *_args: Mock(accepted=True),
        safe_render=Mock(return_value="replay must not render"),
        journal=SQLiteFinalArtifactJournal(db_path),
        composer_fingerprint="composer-v1",
    ).finalize(
        request_id=value.request_id,
        normalized_result=result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=value,
    )

    assert first is None
    assert replay is not None
    assert replay.content == _draft().content
    assert journal.claim_calls == 1
    assert compose.await_count == 0
    assert replay_compose.await_count == 1
    assert safe_render.call_count == 0
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_claim_timeout_requeries_durable_ownership_before_fallback(
    tmp_path,
) -> None:
    value, result = _values("confirmed-claim-deadline-request")
    db_path = tmp_path / "confirmed-claim-deadline.sqlite3"
    deadline = asyncio.timeout(None)

    class ClaimThenDeadlineJournal(SQLiteFinalArtifactJournal):
        async def claim_composition(self, **kwargs):
            acquired = await super().claim_composition(**kwargs)
            assert acquired is True
            deadline.reschedule(asyncio.get_running_loop().time())
            await asyncio.Future()

    safe_render = Mock(return_value="confirmed owner fallback")
    async with deadline:
        final = await FinalCompositionRuntime(
            compose=AsyncMock(return_value=_draft()),
            guard=lambda *_args: Mock(accepted=True),
            safe_render=safe_render,
            journal=ClaimThenDeadlineJournal(db_path),
            composer_fingerprint="composer-v1",
            controlled_deadline_expired=deadline.expired,
        ).finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    assert final is not None
    assert final.content == "confirmed owner fallback"
    assert safe_render.call_count == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_foreign_waiter_deadline_preserves_active_owner_claim_100_times(
    tmp_path,
) -> None:
    db_path = tmp_path / "active-owner.sqlite3"

    class ProcessLocalJournal(SQLiteFinalArtifactJournal):
        def __init__(self) -> None:
            super().__init__(db_path)
            self._local_locks: dict[str, asyncio.Lock] = {}

        def lock_for(self, request_id: str) -> asyncio.Lock:
            return self._local_locks.setdefault(request_id, asyncio.Lock())

    for iteration in range(100):
        value, result = _values(f"active-owner-{iteration}")
        owner_started = asyncio.Event()
        owner_release = asyncio.Event()

        async def owner_compose(
            _value,
            started=owner_started,
            release=owner_release,
        ):
            started.set()
            await release.wait()
            return _draft()

        owner = FinalCompositionRuntime(
            compose=owner_compose,
            guard=lambda *_args: Mock(accepted=True),
            safe_render=lambda: "owner fallback must not render",
            journal=ProcessLocalJournal(),
            composer_fingerprint="composer-v1",
        )
        owner_task = asyncio.create_task(
            owner.finalize(
                request_id=value.request_id,
                normalized_result=result,
                outcome=TerminalOutcome.COMPLETED,
                composition_input=value,
            )
        )
        await owner_started.wait()

        deadline = asyncio.timeout(None)

        class DeadlineWaiterJournal(ProcessLocalJournal):
            async def wait_for_final(self, _deadline=deadline, **kwargs):
                _deadline.reschedule(asyncio.get_running_loop().time())
                await asyncio.Future()

        waiter_compose = AsyncMock(return_value=_draft())
        waiter_safe_render = Mock(return_value="waiter fallback")
        waiter = FinalCompositionRuntime(
            compose=waiter_compose,
            guard=lambda *_args: Mock(accepted=True),
            safe_render=waiter_safe_render,
            journal=DeadlineWaiterJournal(),
            composer_fingerprint="composer-v1",
            controlled_deadline_expired=deadline.expired,
        )
        async with deadline:
            waiter_final = await waiter.finalize(
                request_id=value.request_id,
                normalized_result=result,
                outcome=TerminalOutcome.COMPLETED,
                composition_input=value,
            )

        assert waiter_final is None
        assert waiter_compose.await_count == 0
        assert waiter_safe_render.call_count == 0
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
                (value.request_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM graph_final_composition_claims "
                "WHERE request_id = ?",
                (value.request_id,),
            ).fetchone()[0] == 1

        owner_release.set()
        owner_final = await owner_task
        assert owner_final is not None
        assert owner_final.content == _draft().content

        replay_compose = AsyncMock(return_value="must not compose")
        replay = await FinalCompositionRuntime(
            compose=replay_compose,
            guard=lambda *_args: Mock(accepted=True),
            safe_render=lambda: "must not render",
            journal=ProcessLocalJournal(),
            composer_fingerprint="composer-v1",
        ).finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )
        assert replay == owner_final
        assert replay_compose.await_count == 0

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts"
        ).fetchone()[0] == 100
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_treated_as_controlled_deadline(
    tmp_path,
) -> None:
    value, result = _values("caller-cancel-request")
    compose_started = asyncio.Event()

    async def compose(_value):
        compose_started.set()
        await asyncio.Future()

    runtime = FinalCompositionRuntime(
        compose=compose,
        guard=guard_final_response,
        safe_render=Mock(return_value="must not render"),
        journal=SQLiteFinalArtifactJournal(tmp_path / "caller-cancel.sqlite3"),
        composer_fingerprint="composer-v1",
        controlled_deadline_expired=lambda: False,
    )
    task = asyncio.create_task(
        runtime.finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )
    )
    await compose_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_concurrent_deadline_and_caller_cancellation_is_not_swallowed(
    tmp_path,
) -> None:
    value, result = _values("deadline-caller-cancel-race-request")
    compose_started = asyncio.Event()
    db_path = tmp_path / "deadline-caller-cancel-race.sqlite3"
    safe_render = Mock(return_value="must not render")

    async def compose(_value):
        compose_started.set()
        await asyncio.Future()

    async def run_race() -> None:
        task = asyncio.current_task()
        assert task is not None
        loop = asyncio.get_running_loop()
        deadline = asyncio.timeout(0.01)
        assert deadline.when() is not None
        loop.call_at(deadline.when(), task.cancel)
        runtime = FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=safe_render,
            journal=SQLiteFinalArtifactJournal(db_path),
            composer_fingerprint="composer-v1",
            controlled_deadline_expired=deadline.expired,
        )
        async with deadline:
            await runtime.finalize(
                request_id=value.request_id,
                normalized_result=result,
                outcome=TerminalOutcome.COMPLETED,
                composition_input=value,
            )

    task = asyncio.create_task(run_race())
    with pytest.raises(asyncio.CancelledError):
        await task

    assert compose_started.is_set()
    assert safe_render.call_count == 0
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0


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
async def test_cross_event_loop_runtimes_compose_exactly_once(tmp_path) -> None:
    value, result = _values("cross-loop-request")
    db_path = tmp_path / "cross-loop.sqlite3"
    calls = 0
    calls_lock = threading.Lock()

    async def compose(_value):
        nonlocal calls
        with calls_lock:
            calls += 1
        await asyncio.sleep(0.05)
        return _draft()

    async def finalize():
        return await FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=lambda: "generic fallback",
            journal=SQLiteFinalArtifactJournal(db_path),
            composer_fingerprint="composer-v1",
        ).finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    first, second = await asyncio.gather(
        asyncio.to_thread(lambda: asyncio.run(finalize())),
        asyncio.to_thread(lambda: asyncio.run(finalize())),
    )

    assert calls == 1
    assert first == second


@pytest.mark.asyncio
async def test_abandoned_durable_claim_fails_closed_without_recomposition(
    tmp_path,
) -> None:
    value, result = _values("abandoned-claim-request")
    compose = AsyncMock(return_value=_draft())
    journal = SQLiteFinalArtifactJournal(
        tmp_path / "abandoned.sqlite3",
        timeout_seconds=0.1,
    )
    assert await journal.claim_composition(
        request_id=value.request_id,
        normalized_payload_hash=value.normalized_payload_hash,
        composer_fingerprint="composer-v1",
    ) is True

    with pytest.raises(FinalArtifactInvariantError, match="already claimed"):
        await FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=lambda: "generic fallback",
            journal=journal,
            composer_fingerprint="composer-v1",
            claim_wait_seconds=0.1,
        ).finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    assert compose.await_count == 0
    with sqlite3.connect(tmp_path / "abandoned.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (value.request_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_follower_wait_is_not_limited_by_sqlite_busy_timeout(tmp_path) -> None:
    value, result = _values("slow-owner-request")
    db_path = tmp_path / "slow-owner.sqlite3"
    calls = 0
    calls_lock = threading.Lock()

    async def compose(_value):
        nonlocal calls
        with calls_lock:
            calls += 1
        await asyncio.sleep(0.3)
        return _draft()

    async def finalize():
        return await FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=lambda: "generic fallback",
            journal=SQLiteFinalArtifactJournal(
                db_path,
                timeout_seconds=0.1,
            ),
            composer_fingerprint="composer-v1",
        ).finalize(
            request_id=value.request_id,
            normalized_result=result,
            outcome=TerminalOutcome.COMPLETED,
            composition_input=value,
        )

    first, second = await asyncio.gather(
        asyncio.to_thread(lambda: asyncio.run(finalize())),
        asyncio.to_thread(lambda: asyncio.run(finalize())),
    )

    assert calls == 1
    assert first == second


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
