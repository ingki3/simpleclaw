"""Tests for metrics collector."""

from simpleclaw.logging.metrics import MetricsCollector


class TestMetricsCollector:
    def test_initial_state(self):
        collector = MetricsCollector()
        snapshot = collector.get_snapshot()
        assert snapshot.total_executions == 0
        assert snapshot.error_rate == 0.0

    def test_record_success(self):
        collector = MetricsCollector()
        collector.record_execution(success=True, duration_ms=100, tokens_used=50)
        snapshot = collector.get_snapshot()
        assert snapshot.total_executions == 1
        assert snapshot.successful_executions == 1
        assert snapshot.total_tokens_used == 50
        assert snapshot.total_duration_ms == 100

    def test_record_failure(self):
        collector = MetricsCollector()
        collector.record_execution(success=False, duration_ms=50)
        snapshot = collector.get_snapshot()
        assert snapshot.failed_executions == 1
        assert snapshot.error_rate == 1.0

    def test_error_rate_calculation(self):
        collector = MetricsCollector()
        for _ in range(3):
            collector.record_execution(success=True)
        collector.record_execution(success=False)
        snapshot = collector.get_snapshot()
        assert snapshot.error_rate == 0.25

    def test_sub_agent_spawns(self):
        collector = MetricsCollector()
        collector.record_sub_agent_spawn()
        collector.record_sub_agent_spawn()
        assert collector.get_snapshot().sub_agent_spawns == 2

    def test_active_cron_jobs(self):
        collector = MetricsCollector()
        collector.set_active_cron_jobs(5)
        assert collector.get_snapshot().active_cron_jobs == 5

    def test_reset(self):
        collector = MetricsCollector()
        collector.record_execution(success=True, tokens_used=100)
        collector.reset()
        snapshot = collector.get_snapshot()
        assert snapshot.total_executions == 0
        assert snapshot.total_tokens_used == 0

    def test_snapshot_to_dict(self):
        collector = MetricsCollector()
        collector.record_execution(success=True, duration_ms=42.123)
        d = collector.get_snapshot().to_dict()
        assert isinstance(d, dict)
        assert "total_executions" in d
        assert "timestamp" in d
        # 좀비/누수 메트릭이 직렬화 결과에 포함되어야 한다.
        assert "process_kills_sigterm" in d
        assert "process_kills_sigkill" in d
        assert "process_group_leaks" in d
        assert "zombies_reaped" in d

    def test_record_process_kill_sigterm(self):
        """SIGTERM 정상 종료는 sigterm 카운터를 증가시킨다."""
        collector = MetricsCollector()
        collector.record_process_kill(killed=False, group_alive=False, reaped_zombies=0)
        snap = collector.get_snapshot()
        assert snap.process_kills_sigterm == 1
        assert snap.process_kills_sigkill == 0
        assert snap.process_group_leaks == 0
        assert snap.zombies_reaped == 0

    def test_record_process_kill_sigkill_with_leak(self):
        """SIGKILL 후에도 그룹이 살아있으면 누수 카운터가 증가한다."""
        collector = MetricsCollector()
        collector.record_process_kill(killed=True, group_alive=True, reaped_zombies=3)
        snap = collector.get_snapshot()
        assert snap.process_kills_sigkill == 1
        assert snap.process_group_leaks == 1
        assert snap.zombies_reaped == 3

    def test_reset_clears_process_metrics(self):
        collector = MetricsCollector()
        collector.record_process_kill(killed=True, group_alive=True, reaped_zombies=2)
        collector.reset()
        snap = collector.get_snapshot()
        assert snap.process_kills_sigkill == 0
        assert snap.process_group_leaks == 0
        assert snap.zombies_reaped == 0
