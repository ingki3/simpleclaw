"""LangGraph V4 Core의 asset-specific dependency 재도입을 막는 AST 계약."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
CORE_ROOT = REPO_ROOT / "src/simpleclaw/graph_runtime"
GENERIC_ASSET_IMPORTS = frozenset(
    {
        "simpleclaw.recipes.executor",
        "simpleclaw.recipes.models",
        "simpleclaw.skills.executor",
        "simpleclaw.skills.models",
    }
)
ASSET_NAMESPACES = ("simpleclaw.recipes", "simpleclaw.skills")
CONCRETE_CONTRACT_ID = re.compile(
    r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){2,}\Z"
)


def _static_string(node: ast.AST) -> str | None:
    """AST node가 정적 문자열이면 값을 반환한다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_payload_expression(node: ast.AST) -> bool:
    """직접 payload 또는 envelope.payload 접근인지 판정한다."""
    return (isinstance(node, ast.Name) and node.id == "payload") or (
        isinstance(node, ast.Attribute) and node.attr == "payload"
    )


def _payload_key_accesses(expression: ast.AST) -> list[tuple[int, str]]:
    """제어식에서 opaque payload의 정적 key 해석을 찾는다."""
    accesses: list[tuple[int, str]] = []
    for node in ast.walk(expression):
        if (
            isinstance(node, ast.Subscript)
            and _is_payload_expression(node.value)
            and (key := _static_string(node.slice)) is not None
        ):
            accesses.append((node.lineno, key))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_payload_expression(node.func.value)
            and node.args
            and (key := _static_string(node.args[0])) is not None
        ):
            accesses.append((node.lineno, key))
        if isinstance(node, ast.Compare):
            operands = (node.left, *node.comparators)
            for index, operator in enumerate(node.ops):
                left, right = operands[index : index + 2]
                if (
                    isinstance(operator, (ast.In, ast.NotIn))
                    and _is_payload_expression(right)
                    and (key := _static_string(left)) is not None
                ):
                    accesses.append((node.lineno, key))
    return accesses


def _control_flow_expressions(node: ast.AST) -> tuple[ast.AST, ...]:
    """분기 여부를 결정하는 expression만 반환한다."""
    if isinstance(node, (ast.If, ast.IfExp, ast.While)):
        return (node.test,)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        return tuple(condition for generator in node.generators for condition in generator.ifs)
    if isinstance(node, ast.match_case) and node.guard is not None:
        return (node.guard,)
    return ()


def _imported_modules(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    """일반 import와 from import를 비교 가능한 절대 module 이름으로 펼친다."""
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if node.level or node.module is None:
        return ()
    if node.module in ASSET_NAMESPACES:
        return tuple(f"{node.module}.{alias.name}" for alias in node.names)
    return (node.module,)


def _is_asset_specific_import(module: str) -> bool:
    """Core가 허용한 generic adapter 표면 밖의 asset module인지 판정한다."""
    return (
        any(module.startswith(f"{namespace}.") for namespace in ASSET_NAMESPACES)
        and module not in GENERIC_ASSET_IMPORTS
    )


def _architecture_violations(sources: dict[str, str]) -> list[str]:
    """Static asset 의미, asset별 import, evidence reducer를 AST에서 찾는다."""
    violations: list[str] = []
    for label, source in sources.items():
        tree = ast.parse(source, filename=label)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and CONCRETE_CONTRACT_ID.fullmatch(node.value)
            ):
                violations.append(
                    f"{label}:{node.lineno}:static-contract-id:{node.value}"
                )
            for expression in _control_flow_expressions(node):
                for lineno, key in _payload_key_accesses(expression):
                    violations.append(f"{label}:{lineno}:payload-key-branch:{key}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _imported_modules(node):
                    if not _is_asset_specific_import(module):
                        continue
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
    """Denylist에 없는 payload key branch mutation도 반드시 탐지한다."""
    mutated = {
        "nodes.py": "def dispatch(payload):\n    if payload['comet_token']:\n        return 'x'\n"
    }
    assert _architecture_violations(mutated) == [
        "nodes.py:2:payload-key-branch:comet_token"
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


def test_architecture_guard_kills_plain_import_and_static_contract_id_mutations() -> None:
    """Plain asset import와 alternate contract ID도 구조만으로 탐지한다."""
    mutated = {
        "comet.py": (
            "import simpleclaw.skills.comet\n"
            'CONTRACT = "sports.score.input"\n'
        )
    }
    assert _architecture_violations(mutated) == [
        "comet.py:1:asset-import:simpleclaw.skills.comet",
        "comet.py:2:static-contract-id:sports.score.input",
    ]
