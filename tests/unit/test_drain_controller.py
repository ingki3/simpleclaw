"""DrainController 단위 테스트 (BIZ-442).

검증 범위:
- drain request/clear/status 라이프사이클
- timeout(deadline) 계산과 자동 만료
- active operation 카운터 increment/decrement + 컨텍스트 매니저
- 프로세스 간 공유(같은 state 파일을 보는 두 인스턴스)
- 깨진 state 파일 fail-open
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from simpleclaw.daemon.drain import (
    DRAIN_MAINTENANCE_MESSAGE,
    DrainController,
    DrainState,
)


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "drain_state.json"


class FakeClock:
    """테스트에서 결정적으로 시간을 진행시키는 now 콜백."""

    def __init__(self) -> None:
        self.now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class TestDrainLifecycle:
    def test_idle_by_default(self, state_file):
        controller = DrainController(state_file)
        assert controller.is_draining() is False
        assert controller.state() == DrainState.idle()
        assert controller.maintenance_notice() is None

    def test_request_and_clear(self, state_file):
        controller = DrainController(state_file)
        state = controller.request_drain("deploy BIZ-442", timeout=60)
        assert state.draining is True
        assert state.reason == "deploy BIZ-442"
        assert controller.is_draining() is True
        assert controller.maintenance_notice() == DRAIN_MAINTENANCE_MESSAGE

        controller.clear_drain()
        assert controller.is_draining() is False
        assert not state_file.exists()

    def test_clear_is_idempotent(self, state_file):
        controller = DrainController(state_file)
        controller.clear_drain()  # 파일이 없어도 예외 없이 성공
        assert controller.is_draining() is False

    def test_empty_reason_rejected(self, state_file):
        controller = DrainController(state_file)
        with pytest.raises(ValueError):
            controller.request_drain("   ")

    def test_shared_state_across_instances(self, state_file):
        """deploy script 와 bot 프로세스가 각자 인스턴스로 같은 파일을 공유한다."""
        script_side = DrainController(state_file)
        bot_side = DrainController(state_file)
        script_side.request_drain("restart", timeout=60)
        assert bot_side.is_draining() is True
        script_side.clear_drain()
        assert bot_side.is_draining() is False


class TestTimeout:
    def test_deadline_is_now_plus_timeout(self, state_file):
        clock = FakeClock()
        controller = DrainController(state_file, now=clock)
        state = controller.request_drain("deploy", timeout=90)
        assert state.deadline == (clock.now + timedelta(seconds=90)).isoformat()

    def test_default_timeout_used_when_omitted(self, state_file):
        clock = FakeClock()
        controller = DrainController(state_file, default_timeout=45, now=clock)
        state = controller.request_drain("deploy")
        assert state.deadline == (clock.now + timedelta(seconds=45)).isoformat()

    def test_expires_after_deadline(self, state_file):
        """script 가 clear 없이 죽어도 deadline 후 intake 가 자동 복귀한다."""
        clock = FakeClock()
        controller = DrainController(state_file, now=clock)
        controller.request_drain("deploy", timeout=60)
        assert controller.is_draining() is True
        clock.advance(61)
        assert controller.is_draining() is False
        assert controller.maintenance_notice() is None

    def test_timeout_clamped_to_positive(self, state_file):
        clock = FakeClock()
        controller = DrainController(state_file, now=clock)
        state = controller.request_drain("deploy", timeout=-5)
        # 음수/0 timeout 은 1초로 클램프 — deadline 없는 영구 drain 을 막는다.
        assert state.deadline == (clock.now + timedelta(seconds=1)).isoformat()


class TestCorruptStateFile:
    def test_malformed_json_is_fail_open(self, state_file):
        state_file.write_text("{ not json", encoding="utf-8")
        controller = DrainController(state_file)
        assert controller.is_draining() is False

    def test_missing_deadline_is_ignored(self, state_file):
        """deadline 없는 요청은 만료 안전망이 없으므로 무시한다."""
        state_file.write_text('{"draining": true, "reason": "x"}', encoding="utf-8")
        controller = DrainController(state_file)
        assert controller.is_draining() is False

    def test_non_dict_payload_is_fail_open(self, state_file):
        state_file.write_text('["draining"]', encoding="utf-8")
        controller = DrainController(state_file)
        assert controller.is_draining() is False


class TestActiveOperations:
    def test_begin_end_counts(self, state_file):
        controller = DrainController(state_file)
        assert controller.active_operations() == 0
        controller.begin_operation("turn-a")
        controller.begin_operation("turn-b")
        assert controller.active_operations() == 2
        controller.end_operation("turn-a")
        assert controller.active_operations() == 1

    def test_end_never_goes_negative(self, state_file):
        controller = DrainController(state_file)
        controller.end_operation()
        assert controller.active_operations() == 0

    def test_operation_context_manager(self, state_file):
        controller = DrainController(state_file)
        with controller.operation("message_turn"):
            assert controller.active_operations() == 1
        assert controller.active_operations() == 0

    def test_operation_decrements_on_exception(self, state_file):
        controller = DrainController(state_file)
        with pytest.raises(RuntimeError):
            with controller.operation("message_turn"):
                raise RuntimeError("boom")
        assert controller.active_operations() == 0


class TestStatus:
    def test_status_includes_drain_state_and_active_count(self, state_file):
        controller = DrainController(state_file)
        controller.request_drain("deploy", timeout=60)
        controller.begin_operation()
        status = controller.status()
        assert status["draining"] is True
        assert status["reason"] == "deploy"
        assert status["active_operations"] == 1
        assert status["deadline"] is not None

    def test_status_when_idle(self, state_file):
        controller = DrainController(state_file)
        status = controller.status()
        assert status["draining"] is False
        assert status["active_operations"] == 0
