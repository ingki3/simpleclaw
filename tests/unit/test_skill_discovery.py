"""Tests for skill discovery."""

from pathlib import Path


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

    def test_script_path_inferred_from_single_python_command(self, tmp_path):
        """``## Script``가 없어도 명령 예시의 단일 Python 스크립트를 대표 경로로 추론한다."""
        local = tmp_path / "local"
        local.mkdir()
        skill_dir = local / "news-search-skill"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "news_search.py"
        script.write_text("print('ok')\n", encoding="utf-8")
        venv_python = scripts_dir / "venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\n", encoding="utf-8")

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: news-search-skill\n"
            "description: Search recent news.\n"
            "---\n"
            "# News Search Skill\n\n"
            "```bash\n"
            f"{venv_python} {script} --query \\\"Latest AI news\\\"\n"
            "```\n",
            encoding="utf-8",
        )

        result = discover_skills(local, tmp_path / "no_global")
        skill = next(s for s in result if s.name == "news-search-skill")

        assert skill.script_path == str(script)

    def test_script_path_inferred_from_repeated_skill_dir_variable_command(self, tmp_path):
        """``$SKILL_DIR`` 기반 예시도 같은 단일 스크립트면 대표 경로로 추론한다."""
        local = tmp_path / "local"
        local.mkdir()
        skill_dir = local / "kr-stock-skill"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "kr_stock.py"
        script.write_text("print('ok')\n", encoding="utf-8")

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: kr-stock-skill\n"
            "description: Korean stock lookup.\n"
            "---\n"
            "# Korean Stock Skill\n\n"
            "```bash\n"
            "SKILL_DIR=\\\"/ignored/by/parser\\\"\n"
            "$SKILL_DIR/scripts/venv/bin/python $SKILL_DIR/scripts/kr_stock.py --help\n"
            "$SKILL_DIR/scripts/venv/bin/python $SKILL_DIR/scripts/kr_stock.py search --keywords \\\"삼성전자\\\"\n"
            "```\n",
            encoding="utf-8",
        )

        result = discover_skills(local, tmp_path / "no_global")
        skill = next(s for s in result if s.name == "kr-stock-skill")

        assert skill.script_path == str(script)

    def test_script_path_not_inferred_when_commands_reference_multiple_scripts(self, tmp_path):
        """여러 스크립트가 섞인 문서형 스킬은 임의 대표 스크립트를 고르지 않는다."""
        local = tmp_path / "local"
        local.mkdir()
        skill_dir = local / "office-skill"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "read.py").write_text("print('read')\n", encoding="utf-8")
        (scripts_dir / "write.py").write_text("print('write')\n", encoding="utf-8")

        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: office-skill\n"
            "description: Multiple office helpers.\n"
            "---\n"
            "# Office Skill\n\n"
            "```bash\n"
            "python scripts/read.py input.docx\n"
            "python scripts/write.py output.docx\n"
            "```\n",
            encoding="utf-8",
        )

        result = discover_skills(local, tmp_path / "no_global")
        skill = next(s for s in result if s.name == "office-skill")

        assert skill.script_path == ""

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
