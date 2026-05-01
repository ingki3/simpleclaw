"""Tests for structured logger."""

import json

import pytest

from simpleclaw.logging.structured_logger import StructuredLogger, LogEntry
from simpleclaw.logging.trace_context import reset_trace_id, set_trace_id, trace_scope


class TestStructuredLogger:
    @pytest.fixture
    def logger_instance(self, tmp_path):
        return StructuredLogger(log_dir=tmp_path / "logs")

    def test_log_creates_entry(self, logger_instance):
        entry = logger_instance.log(
            action_type="test_action",
            input_summary="input",
            output_summary="output",
            duration_ms=42.5,
            status="success",
        )
        assert entry.action_type == "test_action"
        assert entry.duration_ms == 42.5
        assert entry.status == "success"
        assert logger_instance.entry_count == 1

    def test_log_writes_to_file(self, logger_instance, tmp_path):
        logger_instance.log(action_type="test")
        log_files = list((tmp_path / "logs").glob("execution_*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text()
        data = json.loads(content.strip())
        assert data["action_type"] == "test"

    def test_log_jsonl_format(self, logger_instance, tmp_path):
        logger_instance.log(action_type="action1")
        logger_instance.log(action_type="action2")
        log_files = list((tmp_path / "logs").glob("execution_*.log"))
        lines = log_files[0].read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["action_type"] == "action1"
        assert json.loads(lines[1])["action_type"] == "action2"

    def test_get_entries(self, logger_instance):
        logger_instance.log(action_type="a1")
        logger_instance.log(action_type="a2")
        entries = logger_instance.get_entries()
        assert len(entries) == 2
        assert entries[0].action_type == "a1"

    def test_get_entries_with_limit(self, logger_instance):
        for i in range(10):
            logger_instance.log(action_type=f"action_{i}")
        entries = logger_instance.get_entries(limit=3)
        assert len(entries) == 3

    def test_get_entries_nonexistent_date(self, logger_instance):
        entries = logger_instance.get_entries(date="19700101")
        assert entries == []

    def test_log_entry_to_json(self):
        entry = LogEntry(
            timestamp="2026-01-01T00:00:00",
            action_type="test",
            status="success",
        )
        data = json.loads(entry.to_json())
        assert data["action_type"] == "test"

    def test_log_truncates_long_input(self, logger_instance):
        long_text = "x" * 1000
        entry = logger_instance.log(
            action_type="test",
            input_summary=long_text,
        )
        assert len(entry.input_summary) == 500


class TestStructuredLoggerTraceId:
    """trace_id 자동 주입과 필터링 동작을 검증한다."""

    @pytest.fixture(autouse=True)
    def _clear_trace(self):
        token = set_trace_id("")
        yield
        reset_trace_id(token)

    @pytest.fixture
    def logger_instance(self, tmp_path):
        return StructuredLogger(log_dir=tmp_path / "logs")

    def test_trace_id_auto_pulled_from_context(self, logger_instance, tmp_path):
        with trace_scope("abc123"):
            entry = logger_instance.log(action_type="test")
        assert entry.trace_id == "abc123"
        # 디스크 직렬화에도 포함되는지 검증
        log_files = list((tmp_path / "logs").glob("execution_*.log"))
        data = json.loads(log_files[0].read_text().strip())
        assert data["trace_id"] == "abc123"

    def test_trace_id_empty_when_no_context(self, logger_instance):
        entry = logger_instance.log(action_type="test")
        assert entry.trace_id == ""

    def test_explicit_trace_id_overrides_context(self, logger_instance):
        with trace_scope("from-ctx"):
            entry = logger_instance.log(action_type="test", trace_id="explicit")
        assert entry.trace_id == "explicit"

    def test_get_entries_filters_by_trace_id(self, logger_instance):
        with trace_scope("trace-A"):
            logger_instance.log(action_type="step1")
            logger_instance.log(action_type="step2")
        with trace_scope("trace-B"):
            logger_instance.log(action_type="step3")

        entries_a = logger_instance.get_entries(trace_id="trace-A")
        assert [e.action_type for e in entries_a] == ["step1", "step2"]

        entries_b = logger_instance.get_entries(trace_id="trace-B")
        assert [e.action_type for e in entries_b] == ["step3"]

    def test_get_entries_handles_legacy_lines_without_trace_id(
        self, logger_instance, tmp_path
    ):
        """trace_id 필드가 없는 구버전 로그도 안전하게 역직렬화되어야 한다."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        date_str = datetime.now().strftime("%Y%m%d")
        legacy_line = json.dumps({
            "timestamp": "2026-01-01T00:00:00",
            "level": "INFO",
            "action_type": "legacy",
            "input_summary": "",
            "output_summary": "",
            "duration_ms": 0.0,
            "status": "success",
            "details": {},
        })
        (log_dir / f"execution_{date_str}.log").write_text(legacy_line + "\n")

        entries = logger_instance.get_entries()
        assert len(entries) == 1
        assert entries[0].action_type == "legacy"
        assert entries[0].trace_id == ""
