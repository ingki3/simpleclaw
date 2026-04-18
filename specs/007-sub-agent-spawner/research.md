# Research: Sub-Agent Dynamic Spawner

## 1. Subprocess Spawning with asyncio

**Decision**: Use `asyncio.create_subprocess_exec` for spawning sub-agents.

**Rationale**: Already used in the project's `CLIProvider` (llm/cli_wrapper.py). Native asyncio integration, non-blocking, supports stdout/stderr capture, timeout handling via `asyncio.wait_for`.

**Alternatives considered**:
- `subprocess.Popen`: Blocking, would require thread pool for async integration.
- `multiprocessing.Process`: Heavier weight, shares memory space concerns, not suited for CLI/script invocation.

## 2. Concurrency Pool

**Decision**: Use `asyncio.Semaphore` for concurrency limiting with an internal queue.

**Rationale**: Semaphore is the standard asyncio primitive for limiting concurrent operations. Combined with a simple list-based queue, it provides FIFO ordering for pending spawn requests. No external dependencies needed.

**Alternatives considered**:
- `asyncio.Queue` + worker tasks: More complex, overkill for 3 concurrent items.
- Thread pool executor: Unnecessary complexity for async subprocess management.

## 3. JSON-over-Stdout Protocol

**Decision**: Sub-agent writes a single JSON object to stdout on completion. All debug/log output goes to stderr.

**Rationale**: Matches PRD specification (section 4.3). Simple, parseable, no framing protocol needed. The main agent reads stdout after process completion and parses the entire output as JSON.

**Format**:
```json
{
  "status": "success",
  "data": { ... },
  "error": null
}
```

Or on failure:
```json
{
  "status": "error",
  "data": null,
  "error": "Description of what went wrong"
}
```

## 4. Permission Scope Injection

**Decision**: Pass permission scope as a JSON environment variable (`AGENT_SCOPE`).

**Rationale**: Environment variables are the simplest cross-process communication for startup parameters. The sub-agent reads `AGENT_SCOPE` on startup to know its constraints. This avoids protocol complexity while being language-agnostic.

**Format**:
```json
{
  "allowed_paths": [".agent/workspace/sub_agents/abc123/"],
  "network": false
}
```

## 5. Workspace Isolation

**Decision**: Create workspace directories under `workspace/sub_agents/{agent_id}/` using UUID-based agent IDs.

**Rationale**: UUID ensures uniqueness. The directory is created before subprocess starts and is included in the permission scope's `allowed_paths`. Cleanup is configurable (default: keep for debugging).

## 6. Timeout and Termination

**Decision**: Use `asyncio.wait_for` with SIGTERM → grace period → SIGKILL pattern.

**Rationale**: Standard Unix process termination pattern. Grace period (default 5s) allows sub-agent to clean up before forced kill. Already proven in the project's CLI wrapper.

## 7. Config Extension

**Decision**: Add `sub_agents` section to `config.yaml`.

**Format**:
```yaml
sub_agents:
  max_concurrent: 3
  default_timeout: 300
  workspace_dir: "workspace/sub_agents"
  cleanup_workspace: false
  default_scope:
    allowed_paths: []
    network: false
```
