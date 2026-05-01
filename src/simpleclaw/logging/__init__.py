"""Structured logging and metrics collection."""

from simpleclaw.logging.structured_logger import StructuredLogger, LogEntry
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.dashboard import DashboardServer
from simpleclaw.logging.trace_context import (
    TRACE_ID_ENV_VAR,
    adopt_env_trace_id,
    get_trace_id,
    inject_trace_id_env,
    new_trace_id,
    set_trace_id,
    trace_scope,
)

__all__ = [
    "DashboardServer",
    "LogEntry",
    "MetricsCollector",
    "StructuredLogger",
    "TRACE_ID_ENV_VAR",
    "adopt_env_trace_id",
    "get_trace_id",
    "inject_trace_id_env",
    "new_trace_id",
    "set_trace_id",
    "trace_scope",
]
