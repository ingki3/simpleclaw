"""BIZ-564 — bounded LangGraph dependency smoke test."""

from __future__ import annotations

from importlib.metadata import version

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph


def test_langgraph_and_sqlite_checkpointer_are_importable_and_bounded() -> None:
    langgraph_version = tuple(map(int, version("langgraph").split(".")[:2]))
    sqlite_version = tuple(
        map(int, version("langgraph-checkpoint-sqlite").split(".")[:2])
    )

    assert (1, 2) <= langgraph_version < (2, 0)
    assert (3, 1) <= sqlite_version < (4, 0)
    assert StateGraph is not None
    assert AsyncSqliteSaver is not None
