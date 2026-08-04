from simpleclaw.graph_runtime.status import CONDITIONAL_EDGE_TABLES


def test_every_registered_conditional_outcome_has_exactly_one_edge() -> None:
    for outcome_enum, edge_table in CONDITIONAL_EDGE_TABLES.items():
        assert len(edge_table) == len(outcome_enum)
        for outcome in outcome_enum:
            assert outcome in edge_table
            assert isinstance(edge_table[outcome], str)
