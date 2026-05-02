"""Tests for the skill executor."""

from pathlib import Path

import pytest

from simpleclaw.skills.models import RetryPolicy, SkillDefinition, SkillScope
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

    @pytest.mark.asyncio
    async def test_timeout_records_metrics(self, tmp_path):
        """타임아웃 시 ``metrics``로 종료 결과가 보고되어야 한다."""
        from simpleclaw.logging.metrics import MetricsCollector

        script = tmp_path / "slow.py"
        script.write_text("import time; time.sleep(10)")
        skill = _make_skill("slow-skill", str(script))
        metrics = MetricsCollector()

        with pytest.raises(SkillTimeoutError):
            await execute_skill(skill, timeout=1, metrics=metrics)

        snap = metrics.get_snapshot()
        # SIGTERM에 정상 응답하는 자식 → sigterm 카운터가 1 증가해야 한다.
        assert snap.process_kills_sigterm + snap.process_kills_sigkill == 1
        assert snap.process_group_leaks == 0

    @pytest.mark.asyncio
    async def test_trace_id_passed_to_subprocess_env(self, tmp_path):
        """현재 컨텍스트의 trace_id가 SIMPLECLAW_TRACE_ID 환경변수로 전달되어야 한다."""
        from simpleclaw.logging.trace_context import TRACE_ID_ENV_VAR, trace_scope

        script = tmp_path / "echo_trace.py"
        script.write_text(
            "import os\n"
            f"print(os.environ.get({TRACE_ID_ENV_VAR!r}, 'MISSING'))\n"
        )
        skill = _make_skill("trace-echo", str(script))

        with trace_scope("test-trace-xyz"):
            result = await execute_skill(skill)
        assert result.success
        assert result.output == "test-trace-xyz"

    @pytest.mark.asyncio
    async def test_attempts_field_is_one_on_first_success(self):
        """기본 동작: 첫 시도에 성공하면 ``attempts == 1``."""
        skill = _make_skill(
            "test-skill",
            str(FIXTURES / "test-skill" / "run.py"),
        )
        result = await execute_skill(skill)
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_trace_id_absent_when_no_context(self, tmp_path):
        """trace_id가 미설정이면 환경변수도 주입되지 않아야 한다."""
        from simpleclaw.logging.trace_context import (
            TRACE_ID_ENV_VAR,
            reset_trace_id,
            set_trace_id,
        )

        script = tmp_path / "echo_trace.py"
        script.write_text(
            "import os\n"
            f"print(os.environ.get({TRACE_ID_ENV_VAR!r}, 'UNSET'))\n"
        )
        skill = _make_skill("trace-echo", str(script))

        token = set_trace_id("")
        try:
            result = await execute_skill(skill)
        finally:
            reset_trace_id(token)
        assert result.success
        assert result.output == "UNSET"


class TestSkillRetry:
    """RetryPolicy 기반 자동 재시도 동작 검증."""

    def _make_flaky_script(
        self,
        tmp_path: Path,
        *,
        fails_until: int,
        marker: str = "flaky",
    ) -> Path:
        """첫 ``fails_until``회는 실패하고 그 이후 성공하는 스크립트를 생성한다.

        호출 횟수는 ``tmp_path`` 내 카운터 파일에 영구화하여 서브프로세스 간 공유한다.
        """
        counter = tmp_path / f"{marker}.count"
        counter.write_text("0")
        script = tmp_path / f"{marker}.py"
        script.write_text(
            "import sys, pathlib\n"
            f"p = pathlib.Path({str(counter)!r})\n"
            "n = int(p.read_text() or '0') + 1\n"
            "p.write_text(str(n))\n"
            f"if n <= {fails_until}:\n"
            "    print('fail attempt %d' % n, file=sys.stderr)\n"
            "    sys.exit(1)\n"
            f"print('ok after %d attempts' % n)\n"
        )
        return script

    @pytest.mark.asyncio
    async def test_no_retry_without_policy(self, tmp_path):
        """정책이 없으면 첫 실패에서 즉시 예외가 발생해야 한다."""
        script = self._make_flaky_script(tmp_path, fails_until=99)
        skill = _make_skill("no-policy", str(script))
        with pytest.raises(SkillExecutionError):
            await execute_skill(skill)
        # 카운터가 1이면 한 번만 시도했다는 의미.
        assert (tmp_path / "flaky.count").read_text() == "1"

    @pytest.mark.asyncio
    async def test_no_retry_when_not_idempotent(self, tmp_path):
        """``idempotent=False`` 정책은 자동 재시도를 활성화하지 않는다."""
        script = self._make_flaky_script(tmp_path, fails_until=99)
        skill = _make_skill("not-idempotent", str(script))
        skill.retry_policy = RetryPolicy(
            max_retries=3,
            idempotent=False,  # 가드가 꺼져 있음
            initial_backoff_seconds=0.0,
        )
        with pytest.raises(SkillExecutionError):
            await execute_skill(skill)
        assert (tmp_path / "flaky.count").read_text() == "1"

    @pytest.mark.asyncio
    async def test_retry_recovers_after_transient_failure(self, tmp_path):
        """첫 시도 실패 후 재시도로 성공하면 결과에 시도 횟수가 반영된다."""
        from simpleclaw.logging.metrics import MetricsCollector

        script = self._make_flaky_script(tmp_path, fails_until=2)
        skill = _make_skill("recoverable", str(script))
        skill.retry_policy = RetryPolicy(
            max_retries=3,
            idempotent=True,
            initial_backoff_seconds=0.0,  # 테스트는 즉시 재시도
            max_backoff_seconds=0.0,
        )
        metrics = MetricsCollector()
        result = await execute_skill(skill, metrics=metrics)
        assert result.success
        assert result.attempts == 3  # 2회 실패 + 1회 성공
        assert "ok after 3 attempts" in result.output

        snap = metrics.get_snapshot()
        assert snap.skill_retries == 2          # 2회 재시도 수행
        assert snap.skill_retry_recovered == 1  # 회복 1건
        assert snap.skill_retry_exhausted == 0

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_and_records_metric(self, tmp_path):
        """재시도 한도를 모두 소진하면 마지막 예외를 던지고 exhausted 메트릭을 올린다."""
        from simpleclaw.logging.metrics import MetricsCollector

        script = self._make_flaky_script(tmp_path, fails_until=99)
        skill = _make_skill("always-fails", str(script))
        skill.retry_policy = RetryPolicy(
            max_retries=2,
            idempotent=True,
            initial_backoff_seconds=0.0,
            max_backoff_seconds=0.0,
        )
        metrics = MetricsCollector()
        with pytest.raises(SkillExecutionError):
            await execute_skill(skill, metrics=metrics)

        # 첫 시도 1회 + 재시도 2회 = 3회 실행.
        assert (tmp_path / "flaky.count").read_text() == "3"
        snap = metrics.get_snapshot()
        assert snap.skill_retries == 2
        assert snap.skill_retry_exhausted == 1
        assert snap.skill_retry_recovered == 0

    @pytest.mark.asyncio
    async def test_timeout_not_retried_by_default(self, tmp_path):
        """``retry_on_timeout=False``(기본값)이면 타임아웃은 즉시 전파된다."""
        from simpleclaw.logging.metrics import MetricsCollector

        script = tmp_path / "slow.py"
        script.write_text("import time; time.sleep(10)")
        skill = _make_skill("slow-no-timeout-retry", str(script))
        skill.retry_policy = RetryPolicy(
            max_retries=3,
            idempotent=True,
            initial_backoff_seconds=0.0,
        )
        metrics = MetricsCollector()
        with pytest.raises(SkillTimeoutError):
            await execute_skill(skill, timeout=1, metrics=metrics)

        snap = metrics.get_snapshot()
        assert snap.skill_retries == 0
        assert snap.skill_retry_exhausted == 0

    @pytest.mark.asyncio
    async def test_backoff_uses_compute_backoff(self, tmp_path, monkeypatch):
        """재시도 사이의 ``asyncio.sleep`` 인자가 정책 백오프 계산과 일치해야 한다."""
        from simpleclaw.skills import executor as executor_mod

        script = self._make_flaky_script(
            tmp_path, fails_until=2, marker="backoff"
        )
        skill = _make_skill("backoff-skill", str(script))
        skill.retry_policy = RetryPolicy(
            max_retries=3,
            idempotent=True,
            initial_backoff_seconds=0.5,
            backoff_factor=2.0,
            max_backoff_seconds=10.0,
        )

        sleeps: list[float] = []
        real_sleep = executor_mod.asyncio.sleep

        async def _fake_sleep(delay):
            sleeps.append(delay)
            await real_sleep(0)  # 이벤트 루프에 양보만

        monkeypatch.setattr(executor_mod.asyncio, "sleep", _fake_sleep)

        result = await execute_skill(skill)
        assert result.success
        assert result.attempts == 3
        # 두 번 재시도하므로 두 번 sleep — 0.5, 1.0
        assert sleeps == [0.5, 1.0]


class TestRetryPolicy:
    """``RetryPolicy`` 헬퍼 메서드의 단위 테스트."""

    def test_disabled_when_not_idempotent(self):
        policy = RetryPolicy(max_retries=5, idempotent=False)
        assert policy.enabled is False

    def test_disabled_when_zero_retries(self):
        policy = RetryPolicy(max_retries=0, idempotent=True)
        assert policy.enabled is False

    def test_enabled_with_idempotent_and_retries(self):
        policy = RetryPolicy(max_retries=3, idempotent=True)
        assert policy.enabled is True

    def test_compute_backoff_exponential(self):
        policy = RetryPolicy(
            initial_backoff_seconds=1.0,
            backoff_factor=2.0,
            max_backoff_seconds=100.0,
        )
        assert policy.compute_backoff(0) == 1.0
        assert policy.compute_backoff(1) == 2.0
        assert policy.compute_backoff(2) == 4.0
        assert policy.compute_backoff(3) == 8.0

    def test_compute_backoff_is_clamped(self):
        policy = RetryPolicy(
            initial_backoff_seconds=1.0,
            backoff_factor=10.0,
            max_backoff_seconds=5.0,
        )
        assert policy.compute_backoff(0) == 1.0
        assert policy.compute_backoff(1) == 5.0  # 10 → clamp to 5
        assert policy.compute_backoff(5) == 5.0  # 100000 → clamp to 5
