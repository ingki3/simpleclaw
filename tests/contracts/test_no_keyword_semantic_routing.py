"""BIZ-523 — ordinary runtime semantic decisions must come from the typed plan."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORDINARY_RUNTIME_FILES = (
    ROOT / "src/simpleclaw/agent/orchestrator.py",
    ROOT / "src/simpleclaw/agent/execution_router.py",
    ROOT / "src/simpleclaw/skills/realtime_lookup.py",
)
FORBIDDEN_SYMBOLS = {
    "_looks_like_live_fact_request",
    "classify_query",
    "infer_domains",
    "infer_intents",
    "classify_response_route",
    "build_turn_frame",
}


def _referenced_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_ordinary_runtime_has_no_keyword_semantic_routing_symbols() -> None:
    violations = {
        str(path.relative_to(ROOT)): sorted(
            _referenced_names(path) & FORBIDDEN_SYMBOLS
        )
        for path in ORDINARY_RUNTIME_FILES
    }
    assert all(not names for names in violations.values()), violations


def test_semantic_few_shots_live_only_in_versioned_examples_prompt() -> None:
    examples_path = (
        ROOT / "prompts/system/unified_turn_planner_examples.yaml"
    )
    data = yaml.safe_load(examples_path.read_text(encoding="utf-8"))
    assert data["name"] == "unified_turn_planner_examples"
    assert isinstance(data["version"], int)
    assert "유해란" in data["template"]

    base_prompt = (
        ROOT / "prompts/system/unified_turn_planner.yaml"
    ).read_text(encoding="utf-8")
    assert "유해란" not in base_prompt
