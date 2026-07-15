"""BIZ-443 — runtime skill 계열 subprocess 의 env scrub 회귀 테스트.

세 실행 경로가 모두 provider/admin secret 을 기본 상속하지 않음을 고정한다:
1. ``skills.executor.execute_skill`` — 등록 스킬 실행 (실제 subprocess 로 검증)
2. ``agent.skill_validate._smoke`` — 운영자 스킬 smoke (subprocess.run mock)
3. ``proactive.context_provider_resolver._run_json_list`` — 스킬 venv provider 호출
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from simpleclaw.skills.executor import execute_skill
from simpleclaw.skills.models import SkillDefinition, SkillScope

# 스킬 subprocess 에 절대 상속되면 안 되는 대표 시크릿 키.
_SECRET_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-x",
    "OPENROUTER_API_KEY": "sk-or-x",
    "ADMIN_API_TOKEN": "admin-tok",
    "TELEGRAM_BOT_TOKEN": "123:abc",
}


def _make_skill(name: str, script: str) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        script_path=script,
        skill_dir=str(Path(script).parent),
        scope=SkillScope.LOCAL,
    )


def _write_env_echo_script(tmp_path: Path) -> Path:
    script = tmp_path / "echo_env.py"
    keys = list(_SECRET_ENV) + ["PATH"]
    script.write_text(
        "import os\n"
        f"for key in {keys!r}:\n"
        "    print(f'{key}=' + ('SET' if os.environ.get(key) else 'MISSING'))\n"
    )
    return script


class TestExecuteSkillEnvScrub:
    @pytest.mark.asyncio
    async def test_runtime_skill_does_not_inherit_provider_admin_secrets(
        self, tmp_path, monkeypatch
    ):
        for key, value in _SECRET_ENV.items():
            monkeypatch.setenv(key, value)
        script = _write_env_echo_script(tmp_path)
        skill = _make_skill("env-scrub", str(script))

        result = await execute_skill(skill, timeout=10)

        for key in _SECRET_ENV:
            assert f"{key}=MISSING" in result.output, key
        # baseline 은 유지되어야 스크립트 실행 자체가 가능하다.
        assert "PATH=SET" in result.output

    @pytest.mark.asyncio
    async def test_explicit_passthrough_is_delivered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
        script = _write_env_echo_script(tmp_path)
        skill = _make_skill("env-passthrough", str(script))

        result = await execute_skill(
            skill, timeout=10, env_passthrough=["OPENROUTER_API_KEY"]
        )

        assert "OPENROUTER_API_KEY=SET" in result.output
        assert "ANTHROPIC_API_KEY=MISSING" in result.output


class TestSkillValidateSmokeEnvScrub:
    def test_smoke_subprocess_receives_scrubbed_env(self, tmp_path, monkeypatch):
        from simpleclaw.agent import skill_validate

        for key, value in _SECRET_ENV.items():
            monkeypatch.setenv(key, value)

        captured: dict = {}

        def fake_run(command, **kwargs):
            captured["env"] = kwargs.get("env")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(skill_validate.subprocess, "run", fake_run)

        script = tmp_path / "tool.py"
        script.write_text("print('hi')\n")
        skill = _make_skill("smoke-env", str(script))

        result = skill_validate._smoke(skill, ["--help"])

        assert result["ok"] is True
        env = captured["env"]
        assert env is not None, "smoke must not inherit the full parent env"
        for key in _SECRET_ENV:
            assert key not in env, key
        assert "PATH" in env


class TestContextProviderEnvScrub:
    def test_run_json_list_receives_scrubbed_env(self, tmp_path, monkeypatch):
        from simpleclaw.proactive import context_provider_resolver as resolver

        for key, value in _SECRET_ENV.items():
            monkeypatch.setenv(key, value)

        captured: dict = {}

        def fake_run(argv, **kwargs):
            captured["env"] = kwargs.get("env")
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        monkeypatch.setattr(resolver.subprocess, "run", fake_run)

        payload = resolver._run_json_list(tmp_path, ["arg"], "print('[]')")

        assert payload == []
        env = captured["env"]
        assert env is not None, "provider call must not inherit the full parent env"
        for key in _SECRET_ENV:
            assert key not in env, key
        assert "PATH" in env
