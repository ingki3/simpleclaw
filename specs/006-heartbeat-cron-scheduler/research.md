# Research: Heartbeat Daemon & Cron Scheduler

## 1. APScheduler for asyncio Daemon

**Decision**: Use APScheduler 3.x with `AsyncIOScheduler` backend.

**Rationale**: APScheduler is a mature, lightweight Python scheduler that integrates natively with asyncio. It supports cron triggers (standard 5-field expressions), interval triggers (for heartbeat), and job persistence via SQLite (via `SQLAlchemyJobStore` or custom store). It aligns with the constitution's "Lightweight Dependencies" principle — no external infrastructure needed.

**Alternatives considered**:
- `schedule` library: Simpler but lacks asyncio support and cron expression parsing.
- `celery-beat`: Violates constitution (heavyweight dependency, requires message broker).
- Custom `asyncio.sleep` loop: Too basic; would need to reinvent cron expression parsing, job persistence, and missed-fire handling.

**Key patterns**:
- Use `AsyncIOScheduler` with `IntervalTrigger` for heartbeat ticks.
- Use `CronTrigger` for user-defined cron jobs.
- Job definitions stored in our own SQLite table (not APScheduler's built-in job store) for full control over CRUD and metadata.
- APScheduler is used purely as a runtime scheduler; our `DaemonStore` owns persistence.

## 2. PID Lock File for Single-Instance Enforcement

**Decision**: Use a PID lock file at a configurable path (default `.agent/daemon.pid`).

**Rationale**: Standard Unix pattern for daemon single-instance enforcement. Simple, no external dependencies, and the daemon removes the file on clean shutdown. On startup, check if the PID in the file is still alive — if not, treat as stale and overwrite.

**Alternatives considered**:
- `fcntl.flock()`: More robust on Unix but less portable and harder to inspect externally.
- Named semaphores: Overkill for single-user, single-machine use.
- Port binding: Unnecessary complexity for a daemon without network endpoints.

## 3. HEARTBEAT.md Format

**Decision**: Write HEARTBEAT.md as a Markdown file with structured status, updating in-place (not appending).

**Rationale**: HEARTBEAT.md serves as a human-readable snapshot of the daemon's current state. Overwriting on each tick keeps the file compact. Historical ticks are not needed (that's what logs are for).

**Format**:
```markdown
# Heartbeat Status

**Last Tick**: 2026-04-18T14:30:00
**Status**: running
**Uptime**: 2h 15m
**Dirty State**: false
**Pending Tasks**: 3
**Last Flush**: 2026-04-18T14:15:00
**Cron Jobs Active**: 5
```

## 4. Dreaming Trigger Conditions

**Decision**: Evaluate dreaming conditions on every heartbeat tick using a dedicated `DreamingTrigger` class.

**Rationale**: The dreaming conditions (2h idle + overnight hour) are checked each tick. This piggybacks on the existing heartbeat cycle without adding a separate scheduler entry. The `DreamingTrigger` queries `ConversationStore` for the last user input timestamp and checks against config values.

**Implementation approach**:
- On each heartbeat tick, call `DreamingTrigger.should_run()`.
- If True, invoke `DreamingPipeline.run()` asynchronously.
- Record last dreaming timestamp in SQLite to prevent same-day re-runs.

## 5. Wait State Serialization

**Decision**: Use JSON serialization for wait states stored in SQLite.

**Rationale**: Wait states need to persist across daemon restarts. JSON is simple, human-readable for debugging, and sufficient for the scope (no complex continuation-passing). Each wait state stores: task_id, serialized_context (JSON), condition_type, timeout, registered_at.

**Limitations**: Cannot serialize arbitrary Python closures or coroutines. Wait states store declarative context (what to resume with), not executable state. The resume handler must reconstruct execution from the saved context.

## 6. Daemon Config Extension

**Decision**: Add a `daemon` section to `config.yaml`.

**Format**:
```yaml
daemon:
  heartbeat_interval: 300    # seconds (5 minutes)
  pid_file: ".agent/daemon.pid"
  status_file: ".agent/HEARTBEAT.md"
  dreaming:
    overnight_hour: 3        # 03:00
    idle_threshold: 7200     # seconds (2 hours)
  wait_state:
    default_timeout: 3600    # seconds (1 hour)
```

## 7. Cron Job SQLite Schema

**Decision**: Use a dedicated `cron_jobs` table in the existing SQLite database pattern.

**Schema**:
- `cron_jobs`: name (PK), cron_expression, action_type, action_reference, enabled, created_at, updated_at
- `cron_executions`: id (PK), job_name (FK), started_at, finished_at, status, result_summary, error_details
- `wait_states`: task_id (PK), serialized_state, condition_type, registered_at, timeout_seconds, resolved_at, resolution
- `daemon_state`: key (PK), value — for storing last_dreaming_timestamp and other singleton state
