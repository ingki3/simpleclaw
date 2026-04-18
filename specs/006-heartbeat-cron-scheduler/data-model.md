# Data Model: Heartbeat Daemon & Cron Scheduler

## Entities

### CronJob
A user-defined scheduled task persisted in SQLite.

| Field | Type | Constraints |
|-------|------|-------------|
| name | str | Primary key, unique, non-empty |
| cron_expression | str | Valid 5-field cron syntax |
| action_type | ActionType (enum) | "prompt" or "recipe" |
| action_reference | str | Prompt text or recipe path |
| enabled | bool | Default True |
| created_at | datetime | Auto-set on creation |
| updated_at | datetime | Auto-set on modification |

### CronJobExecution
A record of a single cron job execution.

| Field | Type | Constraints |
|-------|------|-------------|
| id | int | Primary key, auto-increment |
| job_name | str | Foreign key → CronJob.name |
| started_at | datetime | Non-null |
| finished_at | datetime | Nullable (null if still running) |
| status | ExecutionStatus (enum) | "running", "success", "failure" |
| result_summary | str | Nullable |
| error_details | str | Nullable |

### HeartbeatTick
A snapshot of daemon state at a point in time (transient, written to HEARTBEAT.md).

| Field | Type | Constraints |
|-------|------|-------------|
| timestamp | datetime | Non-null |
| dirty_state | bool | Whether in-memory data has changes |
| pending_task_count | int | >= 0 |
| flush_performed | bool | Whether DB flush happened this tick |
| cron_jobs_active | int | Number of enabled cron jobs |

### WaitState
A serialized paused task persisted in SQLite.

| Field | Type | Constraints |
|-------|------|-------------|
| task_id | str | Primary key, unique |
| serialized_state | str (JSON) | Task context for resumption |
| condition_type | str | Type of wait condition |
| registered_at | datetime | Non-null |
| timeout_seconds | int | > 0 |
| resolved_at | datetime | Nullable |
| resolution | str | Nullable: "completed", "timeout", "error" |

### DaemonState
Key-value singleton state for the daemon.

| Field | Type | Constraints |
|-------|------|-------------|
| key | str | Primary key |
| value | str | JSON-encoded value |

Used for: `last_dreaming_timestamp`, `daemon_start_time`, etc.

## Enums

### ActionType
- `PROMPT` — Execute a prompt string
- `RECIPE` — Execute a recipe by path reference

### ExecutionStatus
- `RUNNING` — Job currently executing
- `SUCCESS` — Job completed successfully
- `FAILURE` — Job encountered an error

## Relationships

- CronJob 1 → N CronJobExecution (one job has many execution records)
- WaitState is standalone (linked to external task systems by task_id)
- DaemonState is a singleton key-value store

## State Transitions

### CronJob Lifecycle
```
Created (enabled=True) → Executing → Completed/Failed → Waiting for next schedule
                       ↘ Disabled (enabled=False) — skips execution
                       ↗ Re-enabled
Deleted — removed from system
```

### WaitState Lifecycle
```
Registered → Waiting → Resolved (completed | timeout | error)
```

### Daemon Lifecycle
```
Stopped → Starting (acquire PID lock) → Running (tick loop) → Stopping (release lock) → Stopped
                                                             ↘ Error → Recovery → Running
```
