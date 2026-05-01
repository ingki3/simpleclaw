"""Tests for skill discovery."""

from pathlib import Path

import pytest

from simpleclaw.skills.models import SkillScope
from simpleclaw.skills.discovery import discover_skills

FIXTURES = Path(__file__).parent.parent / "fixtures" / "skills"


class TestSkillDiscovery:
    def test_discover_from_local(self, tmp_path):
        """Discover skills from local directory only."""
        result = discover_skills(FIXTURES, tmp_path / "no_global")
        assert len(result) == 2
        names = {s.name for s in result}
        assert "test-skill" in names
        assert "another-skill" in names

    def test_discover_from_global(self, tmp_path):
        """Discover skills from global directory only."""
        result = discover_skills(tmp_path / "no_local", FIXTURES)
        assert len(result) == 2
        for skill in result:
            assert skill.scope == SkillScope.GLOBAL

    def test_local_overrides_global(self, tmp_path):
        """Local skill with same name overrides global."""
        global_dir = tmp_path / "global_skills"
        global_dir.mkdir()
        skill_dir = global_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "# test-skill\n\nGlobal version.\n\n## Script\n\nTarget: `run.py`\n",
            encoding="utf-8",
        )

        result = discover_skills(FIXTURES, global_dir)
        test_skill = next(s for s in result if s.name == "test-skill")
        assert test_skill.scope == SkillScope.LOCAL

    def test_empty_directories(self, tmp_path):
        """Both directories missing returns empty list."""
        result = discover_skills(
            tmp_path / "no_local", tmp_path / "no_global"
        )
        assert result == []

    def test_missing_skill_md_skipped(self, tmp_path):
        """Directory without SKILL.md is skipped."""
        local = tmp_path / "local"
        local.mkdir()
        (local / "no-skill-here").mkdir()

        result = discover_skills(local, tmp_path / "no_global")
        assert result == []

    def test_skill_fields_parsed(self):
        """Verify all fields are correctly parsed."""
        result = discover_skills(FIXTURES, Path("/nonexistent"))
        test_skill = next(s for s in result if s.name == "test-skill")

        assert test_skill.description != ""
        assert test_skill.script_path.endswith("run.py")
        assert "test" in test_skill.trigger.lower()
        assert test_skill.scope == SkillScope.LOCAL

    def test_retry_policy_parsed_from_frontmatter(self, tmp_path):
        """프론트매터의 ``retry`` 블록이 ``RetryPolicy``로 파싱되어야 한다."""
        local = tmp_path / "local"
        local.mkdir()
        skill_dir = local / "with-retry"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: with-retry\n"
            "description: A skill with retry policy.\n"
            "retry:\n"
            "  max_retries: 4\n"
            "  initial_backoff_seconds: 0.25\n"
            "  backoff_factor: 3.0\n"
            "  max_backoff_seconds: 20\n"
            "  idempotent: true\n"
            "  retry_on_timeout: true\n"
            "---\n"
            "# with-retry\n",
            encoding="utf-8",
        )
        result = discover_skills(local, tmp_path / "no_global")
        skill = next(s for s in result if s.name == "with-retry")
        assert skill.retry_policy is not None
        assert skill.retry_policy.max_retries == 4
        assert skill.retry_policy.initial_backoff_seconds == 0.25
        assert skill.retry_policy.backoff_factor == 3.0
        assert skill.retry_policy.max_backoff_seconds == 20.0
        assert skill.retry_policy.idempotent is True
        assert skill.retry_policy.retry_on_timeout is True
        assert skill.retry_policy.enabled is True

    def test_retry_policy_absent_by_default(self, tmp_path):
        """``retry`` 블록이 없으면 ``retry_policy``는 None이어야 한다."""
        local = tmp_path / "local"
        local.mkdir()
        skill_dir = local / "no-retry"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: no-retry\n"
            "description: No retry block.\n"
            "---\n"
            "# no-retry\n",
            encoding="utf-8",
        )
        result = discover_skills(local, tmp_path / "no_global")
        skill = next(s for s in result if s.name == "no-retry")
        assert skill.retry_policy is None

    def test_retry_policy_invalid_block_falls_back(self, tmp_path):
        """``retry``가 매핑이 아니면 정책을 비활성화한다 (None)."""
        local = tmp_path / "local"
        local.mkdir()
        skill_dir = local / "bad-retry"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: bad-retry\n"
            "description: Bad retry value.\n"
            "retry: not-a-mapping\n"
            "---\n"
            "# bad-retry\n",
            encoding="utf-8",
        )
        result = discover_skills(local, tmp_path / "no_global")
        skill = next(s for s in result if s.name == "bad-retry")
        assert skill.retry_policy is None
