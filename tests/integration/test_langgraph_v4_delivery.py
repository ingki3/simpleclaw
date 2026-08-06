from __future__ import annotations

import pytest

from simpleclaw.graph_runtime.adapters.delivery import (
    CronDeliveryAdapter,
    SenderReceipt,
    SendNotStartedError,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.graph_runtime.builder import compile_core_graph
from simpleclaw.graph_runtime.composition import FinalCompositionRuntime
from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    ContractRefV1,
    DeliveryIntentV1,
    FinalArtifactV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.idempotency import (
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from simpleclaw.graph_runtime.nodes import (
    CoreNodeCallbacks,
    prepare_delivery_intent,
)
from simpleclaw.graph_runtime.routing import RecipeMatchOutcome, RecipeResultOutcome
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    GraphCompletionRuntime,
    GraphDeliveryContext,
    InMemoryDeliveryJournal,
    InMemoryPersistenceJournal,
    PersistenceRuntime,
    SQLiteDeliveryJournal,
)
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    DeliveryStatus,
    TerminalOutcome,
)
from simpleclaw.memory import ConversationStore


def _result() -> NormalizedAssetResultV1:
    owner = AssetRefV1(type="skill", name="generic")
    return NormalizedAssetResultV1(
        invocation_id="invocation-1",
        output_contract=ContractRefV1(
            contract_id="output",
            version="1",
            owner_ref=owner,
            schema_hash="schema-hash",
        ),
        status=AssetResultStatus.RESOLVED,
        payload={"answer": "verified"},
        payload_hash="payload-hash",
    )


def _core_callbacks(
    *,
    counters: dict[str, int],
    guard_state_observations: list[tuple[bool, bool]],
) -> CoreNodeCallbacks:
    def no_op(_state):
        return {}

    def execute(_state):
        counters["asset_dispatch"] += 1
        return {"normalized_result": _result()}

    def compose_candidate(state):
        guard_state_observations.append(
            ("final_artifact" in state, "delivery_intent" in state)
        )
        return {
            "composition_candidate": "draft",
            "terminal_outcome": TerminalOutcome.COMPLETED,
        }

    return CoreNodeCallbacks(
        normalize_ingress=lambda _state: {"request_id": "request-1"},
        load_existing_context=no_op,
        analyze_request=no_op,
        snapshot_asset_catalogs=no_op,
        match_recipe=lambda _state: {
            "recipe_match": RecipeMatchOutcome.APPLICABLE
        },
        execute_existing_recipe=execute,
        assess_recipe_result=lambda _state: {
            "recipe_result": RecipeResultOutcome.RESOLVED
        },
        select_general_route=no_op,
        simple_conversation=no_op,
        react_subgraph=no_op,
        assess_react_result=no_op,
        deep_research_subgraph=no_op,
        assess_deep_research_result=no_op,
        compose_candidate=compose_candidate,
        resume_user_input=lambda _state, _control: {},
    )


@pytest.mark.asyncio
async def test_recipe_first_graph_connects_guard_delivery_and_store(tmp_path) -> None:
    counters = {
        "asset_dispatch": 0,
        "compose": 0,
        "guard": 0,
        "safe": 0,
        "send": 0,
    }
    pre_guard_state = []

    async def compose(_result):
        counters["compose"] += 1
        return "unsafe draft"

    async def guard(_content):
        counters["guard"] += 1
        return False

    def safe(_result):
        counters["safe"] += 1
        return "안전 응답"

    async def sender(_destination, _content):
        counters["send"] += 1
        return SenderReceipt(external_message_id="telegram-message-1")

    store = ConversationStore(tmp_path / "conversation.db")
    completion = GraphCompletionRuntime(
        composition=FinalCompositionRuntime(
            compose=compose,
            guard=guard,
            safe_render=safe,
        ),
        delivery=DeliveryRuntime(
            journal=SQLiteDeliveryJournal(tmp_path / "delivery.db"),
            adapters={"telegram": TelegramDeliveryAdapter(sender)},
        ),
        persistence=PersistenceRuntime(
            journal=InMemoryPersistenceJournal(),
            writer=ConversationStorePersistenceAdapter(
                store,
                channel="telegram",
            ),
        ),
        resolve_context=lambda _state: GraphDeliveryContext(
            channel="telegram",
            destination_ref="chat-1",
            session_key="session-1",
        ),
    )
    graph = compile_core_graph(
        _core_callbacks(
            counters=counters,
            guard_state_observations=pre_guard_state,
        ),
        completion.callbacks(),
    )

    result = await graph.ainvoke({"ingress": "hello"})

    assert pre_guard_state == [(False, False)]
    assert result["final_artifact"].content == "안전 응답"
    assert result["delivery_receipt"].status is DeliveryStatus.DELIVERED
    assert result["persistence_receipt"].persisted is True
    assert [message.content for message in store.get_recent()] == ["안전 응답"]
    assert counters == {
        "asset_dispatch": 1,
        "compose": 1,
        "guard": 1,
        "safe": 1,
        "send": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("none", DeliveryStatus.UNKNOWN),
        ("empty", DeliveryStatus.UNKNOWN),
        ("failed_before_send", DeliveryStatus.FAILED_BEFORE_SEND),
        ("suppressed", DeliveryStatus.SUPPRESSED),
        ("shadowed", DeliveryStatus.SHADOWED),
    ],
)
async def test_recipe_first_graph_persists_only_delivered_outcome(
    tmp_path,
    mode,
    expected_status,
) -> None:
    counters = {"asset_dispatch": 0}

    async def sender(_destination, _content):
        if mode == "failed_before_send":
            raise SendNotStartedError("preflight failed")
        if mode == "empty":
            return SenderReceipt()
        return None

    channel = "cron" if mode == "suppressed" else "telegram"
    content = "[NO_NOTIFY] answer" if mode == "suppressed" else "answer"
    adapter = (
        CronDeliveryAdapter(sender)
        if channel == "cron"
        else TelegramDeliveryAdapter(sender)
    )

    store = ConversationStore(tmp_path / "conversation.db")
    completion = GraphCompletionRuntime(
        composition=FinalCompositionRuntime(
            compose=lambda _result: content,
            guard=lambda _content: True,
            safe_render=lambda _result: "safe",
        ),
        delivery=DeliveryRuntime(
            journal=SQLiteDeliveryJournal(tmp_path / "delivery.db"),
            adapters={channel: adapter},
        ),
        persistence=PersistenceRuntime(
            journal=InMemoryPersistenceJournal(),
            writer=ConversationStorePersistenceAdapter(
                store,
                channel=channel,
            ),
        ),
        resolve_context=lambda _state: GraphDeliveryContext(
            channel=channel,
            destination_ref="chat-1",
            session_key="session-1",
            shadow=mode == "shadowed",
        ),
    )
    graph = compile_core_graph(
        _core_callbacks(counters=counters, guard_state_observations=[]),
        completion.callbacks(),
    )

    result = await graph.ainvoke({"ingress": "hello"})

    assert result["delivery_receipt"].status is expected_status
    assert "persistence_receipt" not in result
    assert store.get_recent() == []


@pytest.mark.asyncio
async def test_guard_failure_uses_safe_renderer_once_without_redispatch() -> None:
    calls = {"compose": 0, "guard": 0, "safe": 0, "dispatch": 0}

    async def compose(_result):
        calls["compose"] += 1
        return "unsafe draft"

    async def guard(_content):
        calls["guard"] += 1
        return False

    def safe(_result):
        calls["safe"] += 1
        return "안전하게 응답을 제공할 수 없습니다."

    runtime = FinalCompositionRuntime(compose=compose, guard=guard, safe_render=safe)
    final = await runtime.finalize(
        request_id="request-1",
        normalized_result=_result(),
        outcome=TerminalOutcome.COMPLETED,
    )

    assert final is not None
    assert final.content == "안전하게 응답을 제공할 수 없습니다."
    assert final.artifact_id == canonical_artifact_id(
        "request-1", final.content
    )
    assert final.content_hash == canonical_artifact_content_hash(final.content)
    assert await runtime.finalize(
        request_id="request-1",
        normalized_result=_result(),
        outcome=TerminalOutcome.COMPLETED,
    ) == final
    assert calls == {"compose": 1, "guard": 1, "safe": 1, "dispatch": 0}


def test_delivery_intent_rejects_noncanonical_final_artifact() -> None:
    final = FinalArtifactV1(
        artifact_id="arbitrary-artifact",
        request_id="request-1",
        content="hello",
        outcome=TerminalOutcome.COMPLETED,
        content_hash=canonical_artifact_content_hash("hello"),
    )

    with pytest.raises(ValueError, match="identity mismatch"):
        prepare_delivery_intent(
            final,
            channel="telegram",
            destination_ref="chat-1",
        )


@pytest.mark.asyncio
async def test_delivered_persistence_crash_recovers_without_resend() -> None:
    sends = 0
    rows: dict[str, str] = {}
    fail_after_write = True

    async def sender(_destination, _content):
        nonlocal sends
        sends += 1
        return SenderReceipt(external_message_id="message-1")

    async def persist(_session_key, persistence_id, payload_hash, content):
        nonlocal fail_after_write
        existing = rows.get(persistence_id)
        if existing is not None and existing != payload_hash:
            raise ValueError("conflicting persistence payload")
        rows[persistence_id] = payload_hash
        if fail_after_write:
            fail_after_write = False
            raise RuntimeError("crash after durable store write")

    intent = DeliveryIntentV1(
        delivery_id="delivery-1",
        request_id="request-1",
        artifact_id=canonical_artifact_id("request-1", "hello"),
        artifact_hash=canonical_artifact_content_hash("hello"),
        channel="telegram",
        destination_ref="chat-1",
    )
    delivery = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )
    receipt = await delivery.deliver(intent, "hello")
    persistence = PersistenceRuntime(
        journal=InMemoryPersistenceJournal(), writer=persist
    )

    with pytest.raises(RuntimeError, match="crash"):
        await persistence.persist_delivered(
            session_key="session-1",
            request_id="request-1",
            artifact_hash="artifact-hash",
            content="hello",
            delivery_receipt=receipt,
        )
    recovered = await persistence.persist_delivered(
        session_key="session-1",
        request_id="request-1",
        artifact_hash="artifact-hash",
        content="hello",
        delivery_receipt=receipt,
    )

    assert recovered.persisted is True
    assert sends == 1
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.SHADOWED,
        DeliveryStatus.SUPPRESSED,
        DeliveryStatus.FAILED_BEFORE_SEND,
        DeliveryStatus.UNKNOWN,
    ],
)
async def test_non_delivered_receipt_never_persists(status) -> None:
    writes = 0

    async def persist(_session_key, _persistence_id, _payload_hash, _content):
        nonlocal writes
        writes += 1

    persistence = PersistenceRuntime(
        journal=InMemoryPersistenceJournal(), writer=persist
    )
    receipt = persistence.delivery_receipt_for_test(
        delivery_id="delivery-1", status=status
    )

    result = await persistence.persist_delivered(
        session_key="session-1",
        request_id="request-1",
        artifact_hash="artifact-hash",
        content="hello",
        delivery_receipt=receipt,
    )

    assert result is None
    assert writes == 0
