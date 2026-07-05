"""BIZ-425 — skill/recipe capability metadata 파싱 테스트.

metadata contract 의 핵심 계약:
- SKILL.md frontmatter / recipe.yaml 의 ``capability:`` 블록이 파싱된다.
- metadata 미선언/형식 오류 시 보수 기본값(read_only=False, side_effects=True)
  으로 떨어져 자동 실행 후보가 되지 않는다.
"""

from __future__ import annotations

from simpleclaw.capability import CapabilityMetadata, parse_capability_metadata
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.skills.discovery import discover_skills


def _write_skill(tmp_path, name: str, skill_md: str):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")


def test_skill_capability_frontmatter_is_parsed(tmp_path):
    _write_skill(
        tmp_path / "local",
        "sports-skill",
        """---
name: sports-skill
description: Sports lookup.
capability:
  domains: [sports]
  intents: [current_result, standings]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  output_contract: structured_evidence
---
# Sports Skill
""",
    )
    discovered = discover_skills(tmp_path / "local", tmp_path / "global")
    assert len(discovered) == 1
    cap = discovered[0].capability
    assert cap.declared is True
    assert cap.read_only is True
    assert cap.side_effects is False
    assert cap.freshness_sensitive is True
    assert "standings" in cap.intents
    assert "sports" in cap.domains
    assert cap.output_contract == "structured_evidence"
    assert cap.safe_for_auto_execution is True


def test_skill_without_capability_gets_conservative_defaults(tmp_path):
    _write_skill(
        tmp_path / "local",
        "legacy-skill",
        """---
name: legacy-skill
description: No capability metadata.
---
# Legacy Skill
""",
    )
    discovered = discover_skills(tmp_path / "local", tmp_path / "global")
    cap = discovered[0].capability
    assert cap.declared is False
    assert cap.read_only is False
    assert cap.side_effects is True
    assert cap.safe_for_auto_execution is False


def test_recipe_capability_metadata_is_parsed(tmp_path):
    recipe_file = tmp_path / "recipe.yaml"
    recipe_file.write_text(
        """
name: market-daily
description: Market close summary.
capability:
  domains: [market]
  intents: [daily_report, close_summary]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  direct_answer: true
instructions: |
  Summarize the market close.
""",
        encoding="utf-8",
    )
    recipe = load_recipe(recipe_file)
    cap = recipe.capability
    assert cap.declared is True
    assert cap.read_only is True
    assert cap.side_effects is False
    assert cap.direct_answer is True
    assert "daily_report" in cap.intents
    assert cap.safe_for_auto_execution is True


def test_recipe_without_capability_gets_conservative_defaults(tmp_path):
    recipe_file = tmp_path / "recipe.yaml"
    recipe_file.write_text(
        """
name: create-reminder
description: Creates a reminder.
instructions: |
  Create a reminder.
""",
        encoding="utf-8",
    )
    recipe = load_recipe(recipe_file)
    assert recipe.capability.declared is False
    assert recipe.capability.safe_for_auto_execution is False


def test_side_effect_recipe_is_not_safe_for_auto_execution(tmp_path):
    recipe_file = tmp_path / "recipe.yaml"
    recipe_file.write_text(
        """
name: create-reminder
description: Creates a reminder.
capability:
  read_only: false
  side_effects: true
  requires_confirmation: true
instructions: |
  Create a reminder.
""",
        encoding="utf-8",
    )
    recipe = load_recipe(recipe_file)
    assert recipe.capability.declared is True
    assert recipe.capability.safe_for_auto_execution is False


def test_read_only_without_explicit_side_effects_stays_unsafe():
    """side_effects 를 명시하지 않으면 read_only=true 여도 자동 실행 금지."""
    cap = parse_capability_metadata({"read_only": True}, source="test")
    assert cap.read_only is True
    assert cap.side_effects is True
    assert cap.safe_for_auto_execution is False


def test_non_mapping_capability_falls_back_to_defaults(caplog):
    with caplog.at_level("WARNING"):
        cap = parse_capability_metadata("read_only", source="broken.yaml")
    assert cap == CapabilityMetadata()
    assert "broken.yaml" in caplog.text


def test_intents_and_domains_are_normalized_to_lowercase():
    cap = parse_capability_metadata(
        {"domains": ["Sports"], "intents": ["Standings", " current_result "]},
        source="test",
    )
    assert cap.domains == ("sports",)
    assert cap.intents == ("standings", "current_result")
