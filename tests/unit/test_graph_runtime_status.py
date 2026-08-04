from simpleclaw.graph_runtime.status import (
    CONDITIONAL_EDGE_TABLES,
    DeliveryStatus,
    EffectStatus,
    LifecycleStatus,
    StatusTransitionError,
    TerminalOutcome,
    require_legal_transition,
    select_terminal_outcome,
)


def test_legal_and_illegal_transitions_fail_closed() -> None:
    require_legal_transition(LifecycleStatus.NEW, LifecycleStatus.ACTIVE)
    require_legal_transition(EffectStatus.NONE, EffectStatus.NOT_AUTHORIZED)

    try:
        require_legal_transition(LifecycleStatus.NEW, LifecycleStatus.TERMINAL)
    except StatusTransitionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("illegal transition was accepted")


def test_terminal_precedence_and_unknown_effect_override_cancel() -> None:
    assert select_terminal_outcome(
        [TerminalOutcome.COMPLETED, TerminalOutcome.CANCELLED]
    ) is TerminalOutcome.CANCELLED
    assert select_terminal_outcome(
        [TerminalOutcome.CANCELLED], effect_status=EffectStatus.UNKNOWN
    ) is TerminalOutcome.BLOCKED


def test_conditional_edge_tables_are_total_and_single_valued() -> None:
    for enum_type, edges in CONDITIONAL_EDGE_TABLES.items():
        assert set(edges) == set(enum_type)
        assert all(isinstance(edge, str) and edge for edge in edges.values())

    assert CONDITIONAL_EDGE_TABLES[DeliveryStatus][DeliveryStatus.UNKNOWN]
