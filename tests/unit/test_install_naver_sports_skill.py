"""BIZ-560 — 설치된 Naver Sports skill capability discovery 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.install_naver_sports_skill import SKILL_NAME, install
from simpleclaw.skills.discovery import discover_skills


def _installed_skill(tmp_path: Path, mutation: str | None = None):
    global_dir = tmp_path / "global"
    skill_dir = install(global_dir)
    skill_md = skill_dir / "SKILL.md"

    if mutation is not None:
        content = skill_md.read_text(encoding="utf-8")
        _, frontmatter, body = content.split("---", 2)
        metadata = yaml.safe_load(frontmatter)
        capability = metadata.get("capability", {})
        if mutation == "undeclared":
            metadata.pop("capability")
        elif mutation == "write":
            capability["read_only"] = False
        elif mutation == "side_effect":
            capability["side_effects"] = True
        elif mutation == "confirmation":
            capability["requires_confirmation"] = True
        elif mutation == "identity_mismatch":
            metadata["name"] = "lookalike-sports-skill"
        else:  # pragma: no cover - test helper contract
            raise AssertionError(f"unknown mutation: {mutation}")
        skill_md.write_text(
            "---\n"
            + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
            + "---"
            + body,
            encoding="utf-8",
        )

    discovered = discover_skills(tmp_path / "local", global_dir)
    assert len(discovered) == 1
    return discovered[0]


def test_installer_discovery_declares_safe_structured_sports_capability(
    tmp_path: Path,
) -> None:
    skill = _installed_skill(tmp_path)
    capability = skill.capability

    assert skill.name == SKILL_NAME
    assert capability.declared is True
    assert capability.read_only is True
    assert capability.side_effects is False
    assert capability.requires_confirmation is False
    assert capability.domains == ("sports",)
    assert capability.intents == (
        "current_result",
        "completed_result",
        "live_score",
        "standings",
        "ranking",
        "leaderboard",
    )
    assert capability.freshness_sensitive is True
    assert capability.input_contract == "query.v1"
    assert capability.output_contract == "asset_result.v1"
    assert capability.coverage == "full_coverage"
    assert capability.safe_for_auto_execution is True
    assert capability.eligible_for_fast_path is True


@pytest.mark.parametrize(
    "mutation",
    ("undeclared", "write", "side_effect", "confirmation", "identity_mismatch"),
)
def test_installed_malicious_capability_is_not_a_trusted_supporting_asset(
    tmp_path: Path,
    mutation: str,
) -> None:
    skill = _installed_skill(tmp_path, mutation)

    trusted = (
        skill.name == SKILL_NAME
        and skill.capability.safe_for_auto_execution
        and skill.capability.eligible_for_fast_path
    )

    assert trusted is False
