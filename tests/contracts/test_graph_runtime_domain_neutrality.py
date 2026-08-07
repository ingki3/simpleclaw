"""LangGraph V4 Core의 asset-specific dependency 재도입을 막는 AST 계약."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[2]
CORE_ROOT = REPO_ROOT / "src/simpleclaw/graph_runtime"
PRODUCTION_MODULES = (
    REPO_ROOT / "src/simpleclaw/production_assets.py",
    REPO_ROOT / "src/simpleclaw/langgraph_v4_shadow_validation.py",
)
ASSET_ROOT = REPO_ROOT / "runtime_assets"
GENERIC_ASSET_IMPORTS = frozenset(
    {
        "simpleclaw.recipes.bindings",
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
ASSET_PRESENTATION_LITERALS = frozenset(
    {
        "rank",
        "team",
        "played",
        "wins",
        "draws",
        "losses",
        "win_rate",
        "games_behind",
        "순위",
        "팀",
        "경기",
        "승",
        "무",
        "패",
        "승률",
        "게임차",
    }
)


def _static_string(node: ast.AST) -> str | None:
    """AST node가 정적 문자열이면 값을 반환한다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _payload_origin(
    node: ast.AST,
    aliases: dict[str, str | None],
) -> tuple[bool, str | None]:
    """Payload 또는 그 파생 alias인지와 마지막 static key를 반환한다."""
    if isinstance(node, ast.Name) and node.id in aliases:
        return True, aliases[node.id]
    if isinstance(node, ast.Attribute) and node.attr == "payload":
        return True, None
    if isinstance(node, ast.Subscript):
        derived, origin = _payload_origin(node.value, aliases)
        if derived:
            return True, _static_string(node.slice) or origin
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "dict"
            and node.args
        ):
            return _payload_origin(node.args[0], aliases)
        if (
            (
                isinstance(node.func, ast.Name)
                and node.func.id == "cast"
            )
            or (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "typing"
                and node.func.attr == "cast"
            )
        ) and len(node.args) >= 2:
            return _payload_origin(node.args[1], aliases)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "copy"
            and node.func.attr in {"copy", "deepcopy"}
            and node.args
        ):
            return _payload_origin(node.args[0], aliases)
        if isinstance(node.func, ast.Attribute):
            derived, origin = _payload_origin(node.func.value, aliases)
            if derived:
                key = (
                    _static_string(node.args[0])
                    if node.func.attr == "get" and node.args
                    else None
                )
                return True, key or origin
    return False, None


def _scope_nodes(scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef):
    """Nested lexical scope를 제외한 현재 scope node만 순회한다."""
    stack = list(reversed(scope.body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        stack.extend(reversed(tuple(ast.iter_child_nodes(node))))


def _payload_aliases(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str | None]:
    """단순 assignment를 따라 payload와 nested static-key alias를 추적한다."""
    aliases: dict[str, str | None] = {"payload": None}
    assignments = tuple(
        node
        for node in _scope_nodes(scope)
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr)
    )
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            value = assignment.value
            derived, origin = _payload_origin(value, aliases)
            if not derived:
                continue
            targets = (
                tuple(assignment.targets)
                if isinstance(assignment, ast.Assign)
                else (assignment.target,)
            )
            for target in targets:
                if isinstance(target, ast.Name) and (
                    target.id not in aliases or aliases[target.id] != origin
                ):
                    aliases[target.id] = origin
                    changed = True
    return aliases


def _payload_key_accesses(
    node: ast.AST,
    aliases: dict[str, str | None],
) -> list[tuple[int, str]]:
    """단일 AST node에서 opaque payload의 정적 key 해석을 찾는다."""
    accesses: list[tuple[int, str]] = []
    if (
        isinstance(node, ast.Subscript)
        and _payload_origin(node.value, aliases)[0]
        and (key := _static_string(node.slice)) is not None
    ):
        accesses.append((node.lineno, key))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and _payload_origin(node.func.value, aliases)[0]
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
                and _payload_origin(right, aliases)[0]
                and (key := _static_string(left)) is not None
            ):
                accesses.append((node.lineno, key))
    return list(dict.fromkeys(accesses))


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


def _architecture_violations(
    sources: dict[str, str],
    *,
    concrete_literals: frozenset[str] = frozenset(),
) -> list[str]:
    """Static asset 의미, asset별 import, evidence reducer를 AST에서 찾는다."""
    violations: list[str] = []
    for label, source in sources.items():
        tree = ast.parse(source, filename=label)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in ASSET_PRESENTATION_LITERALS
            ):
                violations.append(
                    f"{label}:{node.lineno}:asset-presentation-literal:{node.value}"
                )
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in concrete_literals
            ):
                violations.append(
                    f"{label}:{node.lineno}:static-runtime-asset:{node.value}"
                )
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and CONCRETE_CONTRACT_ID.fullmatch(node.value)
            ):
                violations.append(
                    f"{label}:{node.lineno}:static-contract-id:{node.value}"
                )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _imported_modules(node):
                    if not _is_asset_specific_import(module):
                        continue
                    violations.append(f"{label}:{node.lineno}:asset-import:{module}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.casefold()
                if "evidence" in lowered and "reduc" in lowered:
                    violations.append(f"{label}:{node.lineno}:evidence-reducer:{node.name}")
        scopes = (tree, *(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)))
        for scope in scopes:
            aliases = _payload_aliases(scope)
            for node in _scope_nodes(scope):
                for lineno, key in _payload_key_accesses(node, aliases):
                    violations.append(f"{label}:{lineno}:payload-key-branch:{key}")
    return violations


def _core_sources() -> dict[str, str]:
    """Runtime Core 전체 Python source를 안정적인 상대 경로로 읽는다."""
    return {
        path.relative_to(CORE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(CORE_ROOT.rglob("*.py"))
    }


def _production_sources() -> dict[str, str]:
    sources = _core_sources()
    sources.update(
        {
            path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in PRODUCTION_MODULES
        }
    )
    return sources


def _manifest_literals() -> frozenset[str]:
    """Asset-local manifests에서 concrete identity/path literals를 수집한다."""
    literals: set[str] = set()
    for path in ASSET_ROOT.rglob("runtime-asset.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        asset = raw["asset"]
        literals.update((asset["name"], f"{asset['type']}:{asset['name']}"))
        for item in raw["files"]:
            literals.update((item["source"], item["destination"]))
    return frozenset(literals)


def test_core_has_no_fixture_literal_asset_import_or_evidence_reducer() -> None:
    """새 fixture가 Core의 static registration/branch/import를 만들지 못하게 한다."""
    assert _architecture_violations(_core_sources()) == []


def test_production_installer_and_validator_have_no_concrete_runtime_asset() -> None:
    """Manifest에 추가된 asset identity가 production module에 고정되지 않는다."""
    assert _architecture_violations(
        _production_sources(),
        concrete_literals=_manifest_literals(),
    ) == []


def test_architecture_guard_kills_manifest_literal_in_production_mutation() -> None:
    """새 manifest identity를 Core 상수로 옮기는 mutation을 반드시 탐지한다."""
    literal = next(
        item for item in sorted(_manifest_literals()) if ":" in item
    )
    mutated = {"installer.py": f"ASSET = {literal!r}\n"}

    assert _architecture_violations(
        mutated,
        concrete_literals=_manifest_literals(),
    ) == [f"installer.py:1:static-runtime-asset:{literal}"]


def test_architecture_guard_kills_payload_key_branch_mutation() -> None:
    """Denylist에 없는 payload key branch mutation도 반드시 탐지한다."""
    mutated = {
        "nodes.py": "def dispatch(payload):\n    if payload['comet_token']:\n        return 'x'\n"
    }
    assert _architecture_violations(mutated) == [
        "nodes.py:2:payload-key-branch:comet_token"
    ]


def test_architecture_guard_kills_payload_alias_branch_mutation() -> None:
    """payload alias의 모든 nested static-key read를 탐지한다."""
    mutated = {
        "nodes.py": (
            "def dispatch(result):\n"
            "    envelope = result.payload\n"
            "    data = envelope.get('data')\n"
            "    if envelope.get('schema') == 'asset_result.v1':\n"
            "        return data.get('items') if data.get('ok') else []\n"
        )
    }

    assert _architecture_violations(mutated) == [
        "nodes.py:3:payload-key-branch:data",
        "nodes.py:4:payload-key-branch:schema",
        "nodes.py:5:payload-key-branch:ok",
        "nodes.py:5:payload-key-branch:items",
    ]


def test_architecture_guard_kills_payload_dict_wrapper_mutation() -> None:
    """dict copy가 opaque payload origin을 지우지 못하게 한다."""
    mutated = {
        "nodes.py": (
            "def dispatch(result):\n"
            "    envelope = dict(result.payload)\n"
            "    return envelope.get('schema')\n"
        )
    }

    assert _architecture_violations(mutated) == [
        "nodes.py:3:payload-key-branch:schema"
    ]


def test_architecture_guard_kills_payload_cast_wrapper_mutations() -> None:
    """cast와 typing.cast가 opaque payload origin을 지우지 못하게 한다."""
    mutated = {
        "cast.py": (
            "def dispatch(result):\n"
            "    envelope = cast(dict, result.payload)\n"
            "    return envelope.get('schema')\n"
        ),
        "typing_cast.py": (
            "def dispatch(result):\n"
            "    envelope = typing.cast(dict, result.payload)\n"
            "    return envelope['schema']\n"
        ),
    }

    assert _architecture_violations(mutated) == [
        "cast.py:3:payload-key-branch:schema",
        "typing_cast.py:3:payload-key-branch:schema",
    ]


def test_architecture_guard_kills_payload_direct_return_mutation() -> None:
    """제어식 밖 direct return도 opaque payload key read로 탐지한다."""
    mutated = {
        "nodes.py": (
            "def dispatch(result):\n"
            "    envelope = result.payload\n"
            "    return envelope.get('items')\n"
        )
    }

    assert _architecture_violations(mutated) == [
        "nodes.py:3:payload-key-branch:items"
    ]


def test_architecture_guard_kills_payload_copy_assignment_and_format_mutations() -> None:
    """copy 변형과 assignment/formatting의 static-key read를 함께 탐지한다."""
    mutated = {
        "copy_reads.py": (
            "def dispatch(result):\n"
            "    method_copy = result.payload.copy()\n"
            "    shallow_copy = copy.copy(result.payload)\n"
            "    deep_copy = copy.deepcopy(result.payload)\n"
            "    schema = method_copy.get('schema')\n"
            "    has_effect = 'effect' in method_copy\n"
            "    message = f\"{shallow_copy['status']}\"\n"
            "    return '{}'.format(deep_copy.get('items'))\n"
        )
    }

    assert _architecture_violations(mutated) == [
        "copy_reads.py:5:payload-key-branch:schema",
        "copy_reads.py:6:payload-key-branch:effect",
        "copy_reads.py:7:payload-key-branch:status",
        "copy_reads.py:8:payload-key-branch:items",
    ]


def test_architecture_guard_kills_asset_presentation_vocabulary_mutation() -> None:
    """업무별 field/label mapping이 protected Core로 돌아오지 못하게 한다."""
    mutated = {"renderer.py": "FIELDS = (('rank', '순위'), ('team', '팀'))\n"}

    assert _architecture_violations(mutated) == [
        "renderer.py:1:asset-presentation-literal:rank",
        "renderer.py:1:asset-presentation-literal:순위",
        "renderer.py:1:asset-presentation-literal:team",
        "renderer.py:1:asset-presentation-literal:팀",
    ]


def test_architecture_guard_kills_payload_key_loop_and_match_mutations() -> None:
    """Payload key가 반복 iterable이나 match subject를 결정해도 탐지한다."""
    mutated = {
        "loops.py": (
            "def dispatch(payload):\n"
            "    for item in payload['comet_token']:\n"
            "        pass\n"
            "async def async_dispatch(payload):\n"
            "    async for item in payload.get('aurora_token'):\n"
            "        pass\n"
        ),
        "match.py": (
            "def dispatch(payload):\n"
            "    match payload['meteor_token']:\n"
            "        case _:\n"
            "            pass\n"
        ),
    }
    assert _architecture_violations(mutated) == [
        "loops.py:2:payload-key-branch:comet_token",
        "loops.py:5:payload-key-branch:aurora_token",
        "match.py:2:payload-key-branch:meteor_token",
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
