# Data Model: Sub-Agent Dynamic Spawner

## Entities

### PermissionScope
Constraints applied to a sub-agent at spawn time.

| Field | Type | Constraints |
|-------|------|-------------|
| allowed_paths | list[str] | Filesystem paths the sub-agent may access |
| network | bool | Whether network access is permitted (default False) |

### SubAgent
A running or completed sub-agent subprocess.

| Field | Type | Constraints |
|-------|------|-------------|
| agent_id | str | UUID, unique |
| task | str | Task description |
| command | list[str] | Command and arguments to execute |
| scope | PermissionScope | Injected permissions |
| workspace_path | Path | Sandboxed directory path |
| status | SubAgentStatus | pending/running/success/failure/timeout/killed |
| spawn_time | datetime | When the agent was spawned |
| end_time | datetime | Nullable, when the agent finished |
| exit_code | int | Nullable, process exit code |
| timeout | int | Seconds before forced termination |

### SubAgentResult
Parsed output from a completed sub-agent.

| Field | Type | Constraints |
|-------|------|-------------|
| agent_id | str | UUID reference |
| status | str | "success" or "error" |
| data | dict | Output data (nullable) |
| error | str | Error message (nullable) |
| exit_code | int | Process exit code |
| execution_time | float | Seconds elapsed |

## Enums

### SubAgentStatus
- `PENDING` — queued, waiting for pool slot
- `RUNNING` — subprocess active
- `SUCCESS` — completed with exit code 0 and valid JSON
- `FAILURE` — completed with error
- `TIMEOUT` — killed due to timeout
- `KILLED` — force-terminated during shutdown

## State Transitions

```
PENDING → RUNNING → SUCCESS
                  → FAILURE
                  → TIMEOUT
                  → KILLED
```
