"""BIZ-628 — version-controlled V4 assets의 final prose ownership 금지."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("answer", "content", "text", "summary", "raw", "token", "secret")


def _skill_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    _, frontmatter, _body = text.split("---", maxsplit=2)
    loaded = yaml.safe_load(frontmatter)
    assert isinstance(loaded, dict)
    return loaded


def test_production_v4_assets_declare_only_typed_composition_fields() -> None:
    definitions = [
        *(
            _skill_frontmatter(path)
            for path in sorted((ROOT / "runtime_assets/skills").glob("*/SKILL.md"))
        ),
        *(
            yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted((ROOT / "runtime_assets/recipes").glob("*/recipe.yaml"))
        ),
    ]
    v4_definitions = [
        definition
        for definition in definitions
        if definition.get("capability", {}).get("output_contract")
        == "asset_result.v1"
    ]

    assert v4_definitions

    for definition in v4_definitions:
        schema = definition["output_contract"]["json_schema"]
        visible = tuple(schema.get("x-simpleclaw-composition-fields") or ())
        assert visible
        assert all(
            marker not in set(re.split(r"[-_]", segment.casefold()))
            for path in visible
            for segment in path.split(".")
            for marker in FORBIDDEN
        )


def test_central_composer_never_imports_compat_presentation() -> None:
    central_sources = (
        ROOT / "src/simpleclaw/agent/composition_projection.py",
        ROOT / "src/simpleclaw/agent/final_response_composer.py",
        ROOT / "src/simpleclaw/agent/final_response_guard.py",
        ROOT / "src/simpleclaw/graph_runtime/composition.py",
    )

    assert all(
        "asset_result_presentation" not in path.read_text(encoding="utf-8")
        for path in central_sources
    )


def test_sports_assets_keep_empty_result_as_typed_state_only() -> None:
    skill = (ROOT / "runtime_assets/skills/naver-sports-skill/SKILL.md").read_text(
        encoding="utf-8"
    )
    recipe = (ROOT / "runtime_assets/recipes/sports-live/recipe.yaml").read_text(
        encoding="utf-8"
    )

    assert "empty_reason" in skill
    assert "empty_reason" in recipe
    assert "data.message" not in recipe
    assert "plus an explicit `message`" not in skill
