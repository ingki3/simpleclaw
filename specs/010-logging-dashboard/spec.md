# Feature Specification: Logging & Web Dashboard

**Feature Branch**: `010-logging-dashboard`  
**Created**: 2026-04-18  
**Status**: Draft

## User Scenarios & Testing

### User Story 1 - Structured Execution Logging (Priority: P1)

As a user, I want all agent actions, reasoning, and execution results logged to structured log files, so that I can audit and debug agent behavior.

**Why this priority**: Logging is fundamental observability. Without it, debugging and monitoring are impossible.

**Acceptance Scenarios**:

1. **Given** the agent is running, **When** any action executes (skill, recipe, cron job, sub-agent), **Then** a structured log entry is written to `.logs/execution_YYYYMMDD.log`.
2. **Given** log entries are written, **When** I inspect the log file, **Then** each entry contains timestamp, action type, input summary, output summary, duration, and status.
3. **Given** the agent runs for multiple days, **When** the date changes, **Then** a new log file is created for the new date.

---

### User Story 2 - Web Dashboard (Priority: P2)

As a user, I want a simple web dashboard to view token consumption, error rates, and sub-agent statistics, so that I can monitor system health without reading log files.

**Why this priority**: The dashboard provides visual observability but the system works fully without it.

**Acceptance Scenarios**:

1. **Given** the dashboard server is running, **When** I access it in a browser, **Then** I see key metrics: total tokens used, error count, active cron jobs, sub-agent executions.
2. **Given** log data exists, **When** the dashboard loads, **Then** metrics are calculated from the log files and displayed.

---

### Edge Cases

- What happens when the `.logs/` directory is not writable? Log a warning to stderr and continue without file logging.
- What happens when log files grow very large? Implement optional rotation (configurable max file size).

## Requirements

### Functional Requirements

- **FR-001**: System MUST write structured log entries to daily log files at `.logs/execution_YYYYMMDD.log`.
- **FR-002**: Each log entry MUST include: timestamp, level, action type, input summary, output summary, duration_ms, status.
- **FR-003**: System MUST support JSON log format for machine readability.
- **FR-004**: System MUST provide a metrics collector that tracks token usage, error counts, and execution counts.
- **FR-005**: System MUST provide a lightweight web dashboard served via aiohttp.
- **FR-006**: The dashboard MUST display: token consumption summary, error rate, active cron jobs, recent executions.
- **FR-007**: System MUST handle log directory creation and permission errors gracefully.

## Success Criteria

- **SC-001**: All agent actions produce structured log entries within 100ms.
- **SC-002**: The dashboard loads and displays metrics within 2 seconds.
- **SC-003**: Log files are rotated daily automatically.
- **SC-004**: Metrics are accurate and reflect current system state.

## Assumptions

- The dashboard uses aiohttp (already a dependency) for the web server.
- The dashboard is a simple HTML page served by the agent — no frontend build system.
- Token counting uses the existing tiktoken integration.
- Log format is JSONL (one JSON object per line) for easy parsing.
