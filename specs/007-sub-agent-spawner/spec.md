# Feature Specification: Sub-Agent Dynamic Spawner

**Feature Branch**: `007-sub-agent-spawner`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**: User description: "서브 에이전트 동적 호출 모델: ACP 기반 단발성 협업, asyncio 서브프로세스 스폰, JSON-over-Stdout 통신, 최대 3개 하드리밋, 명시적 권한 스코프 주입. PRD 3.3절, 4.3절 요구사항 충족."

## User Scenarios & Testing

### User Story 1 - Spawn and Execute Sub-Agent (Priority: P1)

As the main agent, I want to spawn a sub-agent as an async subprocess to delegate a complex task, receive its result via structured JSON output, and have the sub-agent terminate upon completion, so that specialized work can be handled in isolation without blocking the main process.

**Why this priority**: Spawning and receiving results from a sub-agent is the core capability. Without this, no delegation or collaboration can occur.

**Independent Test**: Can be fully tested by spawning a sub-agent with a simple task, waiting for its JSON response on stdout, and verifying the result is correctly parsed and returned.

**Acceptance Scenarios**:

1. **Given** the main agent has a task to delegate, **When** it spawns a sub-agent with a task description and permission scope, **Then** the sub-agent starts as an async subprocess and begins executing the task.
2. **Given** a sub-agent is running, **When** it completes its task, **Then** it writes a JSON result to stdout and the process terminates with exit code 0.
3. **Given** a sub-agent is running, **When** it encounters an error, **Then** it writes a JSON error response to stdout (or stderr) and terminates with a non-zero exit code.
4. **Given** a sub-agent has finished, **When** the main agent reads the result, **Then** the result is parsed as structured JSON containing status, output data, and any error details.

---

### User Story 2 - Concurrency Pool Limit (Priority: P1)

As a system operator, I want the system to enforce a hard limit on the number of simultaneously running sub-agents (default 3, configurable), so that system resources are not exhausted by uncontrolled spawning.

**Why this priority**: Without a concurrency limit, the system could crash from resource exhaustion. This is a safety-critical feature equally important as spawning itself.

**Independent Test**: Can be tested by attempting to spawn more sub-agents than the configured limit and verifying the excess requests are queued or rejected with a clear error.

**Acceptance Scenarios**:

1. **Given** fewer than the maximum number of sub-agents are running, **When** a new spawn is requested, **Then** the sub-agent starts immediately.
2. **Given** the maximum number of sub-agents are already running, **When** a new spawn is requested, **Then** the request is queued and waits until a slot becomes available.
3. **Given** a sub-agent finishes while others are queued, **When** the slot opens, **Then** the next queued sub-agent starts automatically.
4. **Given** the concurrency limit is configured in config.yaml, **When** the system starts, **Then** the configured limit is enforced (not the default).

---

### User Story 3 - Permission Scope Injection (Priority: P1)

As a security-conscious operator, I want each sub-agent to receive explicit permission constraints at startup specifying allowed filesystem paths and network access, so that sub-agents cannot access unauthorized resources.

**Why this priority**: Permission scope is a security requirement that must be enforced from the start. Without it, sub-agents could access or modify arbitrary system resources.

**Independent Test**: Can be tested by spawning a sub-agent with restricted permissions and verifying the permission scope is correctly passed as startup parameters.

**Acceptance Scenarios**:

1. **Given** a sub-agent is being spawned, **When** the main agent provides a permission scope (allowed paths, network flag), **Then** the permission scope is injected as a JSON parameter to the sub-agent process.
2. **Given** a sub-agent has a permission scope, **When** it starts, **Then** the scope is available for the sub-agent to self-enforce its constraints.
3. **Given** no explicit permission scope is provided, **When** a sub-agent is spawned, **Then** a default restrictive scope is applied (sandbox directory only, no network access).

---

### User Story 4 - Sandboxed Workspace (Priority: P2)

As the main agent, I want each sub-agent to operate in an isolated workspace directory so that sub-agent file operations do not contaminate the main workspace.

**Why this priority**: Workspace isolation enhances safety but the core spawning mechanism works without it. It is important for production use but not for basic functionality.

**Independent Test**: Can be tested by spawning a sub-agent, verifying its workspace directory is created under the designated sandbox path, and confirming it is cleaned up after completion.

**Acceptance Scenarios**:

1. **Given** a sub-agent is spawned, **When** it starts, **Then** a unique workspace directory is created at `workspace/sub_agents/{agent_id}/`.
2. **Given** a sub-agent has a workspace, **When** it completes, **Then** the workspace directory is optionally cleaned up based on configuration (default: keep for debugging).
3. **Given** multiple sub-agents are running, **When** they operate on files, **Then** each sub-agent only sees its own workspace directory.

---

### Edge Cases

- What happens when a sub-agent process hangs and does not terminate? The system must enforce a configurable timeout and kill the process after the timeout expires, logging the forced termination.
- What happens when a sub-agent writes invalid (non-JSON) output to stdout? The system must detect the parsing failure, report an error, and treat the execution as failed.
- What happens when the system is shutting down while sub-agents are running? All running sub-agents must be terminated gracefully (SIGTERM then SIGKILL after grace period).
- What happens when the sub-agent process crashes (segfault, OOM kill)? The system must detect the abnormal exit, report the crash, and free the concurrency slot.
- What happens when the queued spawn request's caller is cancelled while waiting? The queued request should be removed from the queue without consuming a slot.

## Requirements

### Functional Requirements

- **FR-001**: System MUST spawn sub-agents as async subprocesses using the standard process execution mechanism.
- **FR-002**: System MUST communicate with sub-agents via JSON-over-Stdout protocol: the sub-agent writes a single JSON object to stdout upon completion.
- **FR-003**: System MUST enforce a configurable hard limit on concurrent sub-agents (default 3).
- **FR-004**: System MUST queue spawn requests that exceed the concurrency limit and process them in order when slots become available.
- **FR-005**: System MUST inject a permission scope (allowed filesystem paths, network access flag) as a JSON parameter when spawning each sub-agent.
- **FR-006**: System MUST apply a default restrictive permission scope when none is explicitly provided.
- **FR-007**: System MUST create an isolated workspace directory for each sub-agent at `workspace/sub_agents/{agent_id}/`.
- **FR-008**: System MUST enforce a configurable timeout per sub-agent execution (default 300 seconds) and forcefully terminate processes that exceed it.
- **FR-009**: System MUST parse sub-agent stdout as JSON and report parsing failures as execution errors.
- **FR-010**: System MUST log all sub-agent lifecycle events (spawn, complete, error, timeout, kill).
- **FR-011**: System MUST gracefully terminate all running sub-agents during system shutdown.
- **FR-012**: System MUST free concurrency slots when sub-agents complete, crash, or are terminated.

### Key Entities

- **SubAgent**: A spawned subprocess representing a delegated task. Attributes: agent ID (UUID), task description, permission scope, workspace path, process handle, status, spawn time.
- **PermissionScope**: Constraints applied to a sub-agent. Attributes: allowed filesystem paths (list), network access flag (boolean).
- **SpawnRequest**: A request to create a sub-agent. Attributes: task description, permission scope, timeout, callback/future for result.
- **SubAgentResult**: The structured output from a sub-agent. Attributes: status (success/failure), output data (dict), error message, execution time, exit code.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Sub-agents spawn and return results within 5 seconds for simple tasks under normal conditions.
- **SC-002**: The concurrency limit is never exceeded, even under rapid concurrent spawn requests.
- **SC-003**: Timed-out sub-agents are terminated within 10 seconds of their timeout expiring.
- **SC-004**: All sub-agent lifecycle events are logged with timestamps and context.
- **SC-005**: The system handles at least 10 queued spawn requests without data loss or deadlock.
- **SC-006**: Sub-agent workspace directories are correctly isolated and uniquely named.

## Assumptions

- Sub-agents are Python scripts or external CLI commands that can be invoked via subprocess.
- The JSON-over-Stdout protocol uses a single JSON object written to stdout; all other output goes to stderr.
- Sub-agents are short-lived (seconds to minutes, not hours) and terminate after completing their task.
- The main agent and sub-agents run on the same machine with shared filesystem access.
- Permission scope enforcement is cooperative (the sub-agent reads its scope and self-restricts); hard enforcement via OS-level sandboxing is out of scope.
- The workspace base directory (`workspace/sub_agents/`) is relative to the agent's working directory.
- The concurrency limit, timeout, and workspace settings are read from `config.yaml`.
