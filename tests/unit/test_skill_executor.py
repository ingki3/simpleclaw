"""Tests for the skill executor."""

from pathlib import Path

import pytest

from simpleclaw.skills.models import SkillDefinition, SkillScope
from simpleclaw.skills.executor import execute_skill
from simpleclaw.skills.models import (
    SkillExecutionError,
    SkillNotFoundError,
    SkillTimeoutError,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "skills"


def _make_skill(name: str, script: str, skill_dir: str = "") -> SkillDefinition:
    return SkillDefinition(
        name=name,
        script_path=script,
        skill_dir=skill_dir or str(Path(script).parent),
        scope=SkillScope.LOCAL,
    )


class TestSkillExecutor:
    @pytest.mark.asyncio
    async def test_successful_python_script(self):
        skill = _make_skill(
            "test-skill",
            str(FIXTURES / "test-skill" / "run.py"),
        )
        result = await execute_skill(skill)
        assert result.success
        assert "Test skill executed successfully" in result.output

    @pytest.mark.asyncio
    async def test_with_args(self):
        skill = _make_skill(
            "test-skill",
            str(FIXTURES / "test-skill" / "run.py"),
        )
        result = await execute_skill(skill, args=["--verbose", "file.txt"])
        assert result.success
        assert "--verbose" in result.output
        assert "file.txt" in result.output

    @pytest.mark.asyncio
    async def test_successful_bash_script(self):
        skill = _make_skill(
            "another-skill",
            str(FIXTURES / "another-skill" / "run.sh"),
        )
        result = await execute_skill(skill)
        assert result.success
        assert "Another skill executed" in result.output

    @pytest.mark.asyncio
    async def test_script_not_found(self):
        skill = _make_skill("bad", "/nonexistent/script.py")
        with pytest.raises(SkillNotFoundError):
            await execute_skill(skill)

    @pytest.mark.asyncio
    async def test_no_script_path(self):
        skill = SkillDefinition(name="empty", scope=SkillScope.LOCAL)
        with pytest.raises(SkillNotFoundError):
            await execute_skill(skill)

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self, tmp_path):
        script = tmp_path / "fail.py"
        script.write_text(
            "import sys; print('error output', file=sys.stderr); sys.exit(1)"
        )
        skill = _make_skill("fail-skill", str(script))
        with pytest.raises(SkillExecutionError, match="failed"):
            await execute_skill(skill)

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        script = tmp_path / "slow.py"
        script.write_text("import time; time.sleep(10)")
        skill = _make_skill("slow-skill", str(script))
        with pytest.raises(SkillTimeoutError):
            await execute_skill(skill, timeout=1)
