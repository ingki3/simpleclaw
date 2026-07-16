"""BIZ-443 — cron 실행 경로의 env scrub / provider override 부재 회귀 테스트.

cron 잡의 COMMAND 스텝은 ``recipes.executor._execute_command`` 를 통해서만
subprocess 를 만들며, 그 env 는 ``filter_env()`` 로 scrub 된다. 또한 CronJob
모델에는 provider/base_url/model override 채널이 존재하지 않는다 — LLM 호출은
항상 orchestrator 의 ``process_cron_message`` (프로세스 내 라우터)로만 흐른다.
이 두 성질을 회귀 테스트로 고정한다.
"""

import dataclasses

import pytest

from simpleclaw.daemon.models import CronJob
from simpleclaw.recipes.executor import _execute_command
from simpleclaw.recipes.models import StepStatus

_SECRET_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-x",
    "OPENROUTER_API_KEY": "sk-or-x",
    "ADMIN_API_TOKEN": "admin-tok",
    "TELEGRAM_BOT_TOKEN": "123:abc",
}


class TestCronCommandStepEnvScrub:
    @pytest.mark.asyncio
    async def test_command_step_does_not_inherit_secrets(self, monkeypatch):
        """cron 이 실행하는 recipe COMMAND 스텝 subprocess 는 scrub 된 env 를 받는다."""
        for key, value in _SECRET_ENV.items():
            monkeypatch.setenv(key, value)

        probes = "; ".join(
            f'echo "{key}=${{{key}:-MISSING}}"' for key in _SECRET_ENV
        )
        result = await _execute_command("env-probe", probes, timeout=10)

        assert result.status == StepStatus.SUCCESS
        for key in _SECRET_ENV:
            assert f"{key}=MISSING" in result.output, key

    @pytest.mark.asyncio
    async def test_command_step_keeps_baseline_path(self):
        """scrub 후에도 PATH 등 baseline 은 남아 명령 실행이 가능하다."""
        result = await _execute_command(
            "path-probe", 'echo "PATH=${PATH:+SET}"', timeout=10
        )
        assert result.status == StepStatus.SUCCESS
        assert "PATH=SET" in result.output


class TestCronJobHasNoProviderOverrideChannel:
    def test_cron_job_fields_do_not_include_provider_overrides(self):
        """CronJob 에 provider/base_url/model override 필드가 없음을 고정한다.

        cron 설정 한 줄로 LLM 트래픽을 외부 gateway 로 돌리는(credential exfil)
        채널이 생기지 않도록, 필드 추가 시 이 테스트가 깨져 보안 검토를 강제한다.
        """
        field_names = {f.name for f in dataclasses.fields(CronJob)}

        assert field_names == {
            "name",
            "cron_expression",
            "action_type",
            "action_reference",
            "enabled",
            "created_at",
            "updated_at",
            "max_attempts",
            "backoff_seconds",
            "backoff_strategy",
            "circuit_break_threshold",
            "consecutive_failures",
            "run_once",
            "expires_at",
            "max_runs",
            "run_count",
        }
        for forbidden in ("provider", "base_url", "model", "api_key", "endpoint"):
            assert not any(forbidden in name for name in field_names), forbidden
