"""Structured logging and metrics collection."""

from simpleclaw.logging.dashboard import DashboardServer, register_dashboard_routes
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import LogEntry, StructuredLogger
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
    "TRACE_ID_ENV_VAR",
    "DashboardServer",
    "LogEntry",
    "MetricsCollector",
    "StructuredLogger",
    "adopt_env_trace_id",
    "get_trace_id",
    "inject_trace_id_env",
    "new_trace_id",
    "register_dashboard_routes",
    "set_trace_id",
    "trace_scope",
]
