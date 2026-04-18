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
