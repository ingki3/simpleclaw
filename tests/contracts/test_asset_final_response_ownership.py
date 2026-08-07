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
    definitions = (
        _skill_frontmatter(
            ROOT / "runtime_assets/skills/naver-sports-skill/SKILL.md"
        ),
        yaml.safe_load(
            (ROOT / "runtime_assets/recipes/sports-live/recipe.yaml").read_text(
                encoding="utf-8"
            )
        ),
    )

    for definition in definitions:
        schema = definition["output_contract"]["json_schema"]
        visible = tuple(schema.get("x-simpleclaw-composition-fields") or ())
        assert visible
        assert all(
            marker not in set(re.split(r"[-_]", segment.casefold()))
            for path in visible
            for segment in path.split(".")
            for marker in FORBIDDEN
        )
