"""drain_restart_simpleclaw.py 시퀀스 테스트 (BIZ-442).

launchctl/HTTP/sleep 을 전부 주입 fake 로 바꿔 drain → poll → kickstart →
health smoke → evidence 기록 → drain 해제 순서를 검증한다.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "deploy"
    / "drain_restart_simpleclaw.py"
)


@pytest.fixture(scope="module")
def script_module():
    """``scripts/`` 는 PYTHONPATH 에 없으므로 importlib 으로 직접 로드한다."""
    spec = importlib.util.spec_from_file_location(
        "drain_restart_simpleclaw", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
daemon:
  drain:
    state_file: "{tmp_path}/drain_state.json"
    default_timeout: 30

review:
  verification_ledger:
    path: "{tmp_path}/verification_ledger.jsonl"
    retention_days: 90
""")
    return cfg


class FakeTime:
    """sleep 이 시간을 진행시키는 결정적 clock — 폴링 deadline 검증용."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _make_runner(calls: list[list[str]], *, returncode: int = 0, drain_file=None):
    """launchctl 호출을 기록하는 fake subprocess.run."""

    def runner(argv, capture_output=True, text=True):
        calls.append(list(argv))
        if drain_file is not None:
            # kickstart 시점에 drain 이 아직 걸려 있어야 한다(순서 검증).
            assert drain_file.exists(), "kickstart ran without an active drain"
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="")

    return runner


def _make_http(responses: list[tuple[int | None, dict | None]]):
    """폴링 순서대로 응답을 소진하는 fake http_get_json — 소진 후 마지막 응답 반복."""

    def http_get_json(url, token, timeout=5.0):
        if len(responses) > 1:
            return responses.pop(0)
        return responses[0]

    return http_get_json


class TestHappyPath:
    def test_full_sequence(self, script_module, config_file, tmp_path):
        drain_file = tmp_path / "drain_state.json"
        clock = FakeTime()
        launchctl_calls: list[list[str]] = []
        # 폴링: active 2 → 1 → 0 (quiesce), 이후 smoke: status ok.
        http = _make_http(
            [
                (200, {"status": "ok", "drain": {"active_operations": 2}}),
                (200, {"status": "ok", "drain": {"active_operations": 1}}),
                (200, {"status": "ok", "drain": {"active_operations": 0}}),
                (200, {"status": "ok", "drain": {"active_operations": 0}}),
            ]
        )
        args = script_module.build_parser().parse_args(
            [
                "--config", str(config_file),
                "--issue-id", "BIZ-442",
                "--admin-url", "http://127.0.0.1:8082",
                "--admin-token", "test-token",
                "--label", "com.simpleclaw.bot",
            ]
        )

        exit_code = script_module.run_drain_restart(
            args,
            runner=_make_runner(launchctl_calls, drain_file=drain_file),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            http_get_json=http,
            getuid=lambda: 501,
        )

        assert exit_code == 0
        # kickstart 가 올바른 target 으로 1회 호출됐다.
        assert launchctl_calls == [
            ["launchctl", "kickstart", "-k", "gui/501/com.simpleclaw.bot"]
        ]
        # 종료 후 drain 은 해제됐다.
        assert not drain_file.exists()
        # evidence 가 restart/health_smoke stage 로 기록됐다.
        ledger_lines = [
            json.loads(line)
            for line in (tmp_path / "verification_ledger.jsonl")
            .read_text()
            .splitlines()
            if line.strip()
        ]
        by_stage = {r["stage"]: r for r in ledger_lines}
        assert by_stage["restart"]["status"] == "passed"
        assert by_stage["restart"]["issue_id"] == "BIZ-442"
        assert by_stage["health_smoke"]["status"] == "passed"
        assert by_stage["restart"]["source"] == "drain_restart_script"

    def test_no_issue_id_skips_evidence(self, script_module, config_file, tmp_path):
        clock = FakeTime()
        http = _make_http(
            [(200, {"status": "ok", "drain": {"active_operations": 0}})]
        )
        args = script_module.build_parser().parse_args(
            [
                "--config", str(config_file),
                "--admin-url", "http://127.0.0.1:8082",
                "--admin-token", "t",
            ]
        )
        exit_code = script_module.run_drain_restart(
            args,
            runner=_make_runner([]),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            http_get_json=http,
            getuid=lambda: 501,
        )
        assert exit_code == 0
        assert not (tmp_path / "verification_ledger.jsonl").exists()


class TestTimeoutDecision:
    def test_abort_skips_kickstart_and_clears_drain(
        self, script_module, config_file, tmp_path
    ):
        drain_file = tmp_path / "drain_state.json"
        clock = FakeTime()
        launchctl_calls: list[list[str]] = []
        # active 가 영원히 1 — drain 창(5초) 안에 quiesce 실패.
        http = _make_http(
            [(200, {"status": "ok", "drain": {"active_operations": 1}})]
        )
        args = script_module.build_parser().parse_args(
            [
                "--config", str(config_file),
                "--admin-url", "http://127.0.0.1:8082",
                "--admin-token", "t",
                "--drain-timeout", "5",
                "--poll-interval", "2",
                "--on-timeout", "abort",
            ]
        )

        exit_code = script_module.run_drain_restart(
            args,
            runner=_make_runner(launchctl_calls),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            http_get_json=http,
            getuid=lambda: 501,
        )

        assert exit_code == 1
        assert launchctl_calls == []  # restart 는 실행되지 않았다.
        assert not drain_file.exists()  # abort 여도 drain 은 해제된다.

    def test_proceed_restarts_despite_active_operations(
        self, script_module, config_file
    ):
        clock = FakeTime()
        launchctl_calls: list[list[str]] = []
        responses = [
            (200, {"status": "ok", "drain": {"active_operations": 1}}),
        ]

        def http(url, token, timeout=5.0):
            # smoke 단계(재시작 후)에는 ok 를 돌려준다.
            if launchctl_calls:
                return (200, {"status": "ok", "drain": {"active_operations": 0}})
            return responses[0]

        args = script_module.build_parser().parse_args(
            [
                "--config", str(config_file),
                "--admin-url", "http://127.0.0.1:8082",
                "--admin-token", "t",
                "--drain-timeout", "5",
                "--poll-interval", "2",
                "--on-timeout", "proceed",
            ]
        )
        exit_code = script_module.run_drain_restart(
            args,
            runner=_make_runner(launchctl_calls),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            http_get_json=http,
            getuid=lambda: 501,
        )
        assert exit_code == 0
        assert len(launchctl_calls) == 1


class TestFailurePaths:
    def test_kickstart_failure_records_failed_evidence(
        self, script_module, config_file, tmp_path
    ):
        clock = FakeTime()
        http = _make_http(
            [(200, {"status": "ok", "drain": {"active_operations": 0}})]
        )
        args = script_module.build_parser().parse_args(
            [
                "--config", str(config_file),
                "--issue-id", "BIZ-442",
                "--admin-url", "http://127.0.0.1:8082",
                "--admin-token", "t",
            ]
        )
        exit_code = script_module.run_drain_restart(
            args,
            runner=_make_runner([], returncode=1),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            http_get_json=http,
            getuid=lambda: 501,
        )
        assert exit_code == 1
        ledger_lines = [
            json.loads(line)
            for line in (tmp_path / "verification_ledger.jsonl")
            .read_text()
            .splitlines()
            if line.strip()
        ]
        by_stage = {r["stage"]: r for r in ledger_lines}
        assert by_stage["restart"]["status"] == "failed"
        assert by_stage["health_smoke"]["status"] == "failed"

    def test_health_unavailable_falls_back_to_grace_wait(
        self, script_module, config_file
    ):
        """admin health 폴링 불가 시 grace 대기 후 재시작을 진행한다."""
        clock = FakeTime()
        launchctl_calls: list[list[str]] = []
        # quiesce 폴링은 실패(None), smoke 는 성공.
        state = {"restarted": False}

        def http(url, token, timeout=5.0):
            if state["restarted"]:
                return (200, {"status": "ok"})
            return (None, None)

        def runner(argv, capture_output=True, text=True):
            launchctl_calls.append(list(argv))
            state["restarted"] = True
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        args = script_module.build_parser().parse_args(
            [
                "--config", str(config_file),
                "--admin-url", "http://127.0.0.1:8082",
                "--admin-token", "t",
                "--grace-seconds", "3",
            ]
        )
        exit_code = script_module.run_drain_restart(
            args,
            runner=runner,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            http_get_json=http,
            getuid=lambda: 501,
        )
        assert exit_code == 0
        assert len(launchctl_calls) == 1
        assert 3.0 in clock.sleeps  # grace 대기가 실제로 일어났다.
