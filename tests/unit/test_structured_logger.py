"""Tests for structured logger."""

import json

import pytest

from simpleclaw.logging.structured_logger import StructuredLogger, LogEntry


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
