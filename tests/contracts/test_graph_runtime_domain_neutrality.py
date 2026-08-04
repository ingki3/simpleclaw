"""LangGraph V4 Core의 asset-specific dependency 재도입을 막는 AST 계약."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
CORE_ROOT = REPO_ROOT / "src/simpleclaw/graph_runtime"
FIXTURE_LITERALS = frozenset(
    {
        "contract-fixture-workflow",
        "contract-fixture-step",
        "recipe.contract-fixture-workflow.input",
        "skill.contract-fixture-step.input",
        "fixture_key",
        "operation_value",
    }
)
GENERIC_ASSET_IMPORTS = frozenset(
    {
        "simpleclaw.recipes.executor",
        "simpleclaw.recipes.models",
        "simpleclaw.skills.executor",
        "simpleclaw.skills.models",
    }
)


def _architecture_violations(sources: dict[str, str]) -> list[str]:
    """Static fixture ID/key, asset별 import, evidence reducer를 AST에서 찾는다."""
    violations: list[str] = []
    for label, source in sources.items():
        tree = ast.parse(source, filename=label)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for literal in FIXTURE_LITERALS:
                    if literal in node.value:
                        violations.append(f"{label}:{node.lineno}:literal:{literal}")
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if (
                    module.startswith(("simpleclaw.recipes.", "simpleclaw.skills."))
                    and module not in GENERIC_ASSET_IMPORTS
                ):
                    violations.append(f"{label}:{node.lineno}:asset-import:{module}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.casefold()
                if "evidence" in lowered and "reduc" in lowered:
                    violations.append(f"{label}:{node.lineno}:evidence-reducer:{node.name}")
    return violations


def _core_sources() -> dict[str, str]:
    """Runtime Core 전체 Python source를 안정적인 상대 경로로 읽는다."""
    return {
        path.relative_to(CORE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(CORE_ROOT.rglob("*.py"))
    }


def test_core_has_no_fixture_literal_asset_import_or_evidence_reducer() -> None:
    """새 fixture가 Core의 static registration/branch/import를 만들지 못하게 한다."""
    assert _architecture_violations(_core_sources()) == []


def test_architecture_guard_kills_payload_key_branch_mutation() -> None:
    """Fixture key를 읽는 Core branch mutation이 반드시 탐지되는지 자체 검증한다."""
    mutated = {
        "nodes.py": "def dispatch(payload):\n    if payload['fixture_key']:\n        return 'x'\n"
    }
    assert _architecture_violations(mutated) == [
        "nodes.py:2:literal:fixture_key"
    ]


def test_architecture_guard_kills_asset_import_and_evidence_reducer_mutations() -> None:
    """업무별 import와 graph evidence reducer mutation을 각각 탐지한다."""
    mutated = {
        "sports.py": (
            "from simpleclaw.skills.sports import SportsSkill\n"
            "def reduce_evidence(items):\n"
            "    return items\n"
        )
    }
    assert _architecture_violations(mutated) == [
        "sports.py:1:asset-import:simpleclaw.skills.sports",
        "sports.py:2:evidence-reducer:reduce_evidence",
    ]
