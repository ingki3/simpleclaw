CREATE TABLE IF NOT EXISTS llm_usage_events (
    event_id TEXT PRIMARY KEY,
    occurred_at_utc TEXT NOT NULL,
    trace_id TEXT NOT NULL DEFAULT '',
    backend_name TEXT NOT NULL,
    provider_profile TEXT NOT NULL,
    model TEXT NOT NULL,
    route_name TEXT NOT NULL,
    task_name TEXT NOT NULL,
    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('primary', 'retry')),
    retry_reason TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'empty', 'error')),
    duration_ms REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_input_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    usage_known INTEGER NOT NULL CHECK (usage_known IN (0, 1)),
    provider_reported_cost_microusd INTEGER,
    estimated_cost_microusd INTEGER,
    pricing_version TEXT,
    error_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_occurred ON llm_usage_events(occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_backend_model ON llm_usage_events(backend_name, model, occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_route_task ON llm_usage_events(route_name, task_name, occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_trace ON llm_usage_events(trace_id, occurred_at_utc);

CREATE TABLE IF NOT EXISTS llm_usage_alert_claims (
    period_kind TEXT NOT NULL CHECK (period_kind IN ('day', 'month')),
    period_key TEXT NOT NULL,
    threshold_microusd INTEGER NOT NULL,
    claimed_at_utc TEXT NOT NULL,
    observed_cost_microusd INTEGER NOT NULL,
    dispatch_status TEXT NOT NULL DEFAULT 'pending' CHECK (dispatch_status IN ('pending', 'sent', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 1),
    lease_expires_at_utc TEXT,
    next_attempt_at_utc TEXT,
    error_type TEXT,
    PRIMARY KEY (period_kind, period_key, threshold_microusd)
);
