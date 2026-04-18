# Feature Specification: Heartbeat Daemon & Cron Scheduler

**Feature Branch**: `006-heartbeat-cron-scheduler`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**: User description: "데몬 프로세스 상주: 5분 주기 Heartbeat 상태 모니터링 및 Cron 스케줄러. asyncio 이벤트 루프와 APScheduler 기반의 데몬으로 상주하며, HEARTBEAT.md 자율 틱 기반 백그라운드 상태 모니터링, Cron Job 생성/조회/수정/삭제 기능, 드리밍 파이프라인 자동 트리거(마지막 입력 2시간 초과 + 심야 03:00 조건), 비동기 대기 상태 지원. PRD 3.5절, 4.3절 요구사항 충족."

## User Scenarios & Testing

### User Story 1 - Daemon Startup & Heartbeat Monitoring (Priority: P1)

As a system operator, I want the agent to run as a persistent background daemon that periodically checks system health and records its status, so that I can confirm the agent is alive and review its operational state at any time.

**Why this priority**: The heartbeat daemon is the foundational runtime for all other autonomous features. Without a persistent process, no scheduled tasks, dreaming triggers, or background operations can function.

**Independent Test**: Can be fully tested by starting the daemon, waiting for multiple tick cycles, and verifying that the status file is updated with current timestamps, resource metrics, and dirty-state detection.

**Acceptance Scenarios**:

1. **Given** the daemon is not running, **When** the user starts the daemon process, **Then** the daemon initializes, begins its tick cycle at the configured interval (default 5 minutes), and writes an initial status entry.
2. **Given** the daemon is running, **When** a tick fires, **Then** the system records the current timestamp, memory state (dirty/clean), and pending task count in the status file.
3. **Given** the daemon is running and memory data has changed (dirty state), **When** a tick fires, **Then** the system flushes accumulated data to the local database.
4. **Given** the daemon is running and memory data has not changed (clean state), **When** a tick fires, **Then** the system skips the database flush to conserve resources.
5. **Given** the daemon encounters an unrecoverable error during a tick, **When** the error occurs, **Then** the daemon logs the error, skips the current tick, and continues with the next scheduled tick without crashing.

---

### User Story 2 - Cron Job Management (Priority: P1)

As a user, I want to create, list, update, and delete scheduled jobs so that the agent automatically runs specific prompts or recipes at designated times without my manual intervention.

**Why this priority**: Cron scheduling is the core scheduling capability that enables all time-based automation — it is equally critical as the daemon itself.

**Independent Test**: Can be fully tested by creating a cron job with a schedule expression, listing all jobs, modifying the schedule, deleting the job, and verifying each operation persists across daemon restarts.

**Acceptance Scenarios**:

1. **Given** the daemon is running, **When** the user creates a new cron job with a name, schedule expression, and target action (prompt or recipe reference), **Then** the job is persisted and begins executing at the specified times.
2. **Given** one or more cron jobs exist, **When** the user requests a list of all jobs, **Then** the system returns each job's name, schedule, target action, last run time, next run time, and enabled/disabled status.
3. **Given** a cron job exists, **When** the user updates its schedule or target action, **Then** the changes take effect immediately and persist across restarts.
4. **Given** a cron job exists, **When** the user deletes it, **Then** the job is removed and no longer executes.
5. **Given** a cron job is scheduled, **When** the scheduled time arrives, **Then** the target action (prompt or recipe) executes and the result is logged.
6. **Given** a cron job execution fails, **When** the failure occurs, **Then** the error is logged with context, the job remains active, and the next scheduled execution proceeds normally.

---

### User Story 3 - Dreaming Pipeline Auto-Trigger (Priority: P2)

As a user, I want the dreaming (memory consolidation) process to trigger automatically when conditions are met, so that my agent's long-term memory stays current without manual intervention.

**Why this priority**: Dreaming automation depends on the heartbeat daemon (US1) being functional. It enhances the agent's autonomy but is not required for basic scheduling.

**Independent Test**: Can be tested by simulating conversation activity, advancing time past the 2-hour idle threshold and the designated overnight window, and verifying that the dreaming pipeline executes and updates the memory file.

**Acceptance Scenarios**:

1. **Given** conversations have occurred and the last user input was more than 2 hours ago, **When** the current time passes the configured overnight hour (default 03:00), **Then** the dreaming pipeline runs automatically.
2. **Given** conversations have occurred but the last user input was less than 2 hours ago, **When** the overnight hour passes, **Then** the dreaming pipeline does NOT run.
3. **Given** no new conversations have occurred since the last dreaming session, **When** both conditions are met, **Then** the dreaming pipeline does NOT run (nothing new to process).
4. **Given** the dreaming pipeline is triggered, **When** it completes successfully, **Then** the system logs the result and records the last dreaming timestamp to prevent duplicate runs within the same day.

---

### User Story 4 - Async Wait States (Priority: P3)

As a user, I want the agent to pause a task that is waiting for an external response or callback, and resume it when the condition is met, so that the agent can work on other tasks efficiently while waiting.

**Why this priority**: Async wait states are an advanced orchestration feature that builds on top of the daemon and scheduler. The core system functions without it.

**Independent Test**: Can be tested by creating a task that enters a wait state, verifying the agent can process other work, then triggering the resume condition and confirming the original task completes from where it paused.

**Acceptance Scenarios**:

1. **Given** a running task encounters a wait condition (external callback pending), **When** the wait is registered, **Then** the task state is serialized and persisted, and the daemon frees resources to handle other work.
2. **Given** a task is in a wait state, **When** the resume condition is met (callback received or timeout elapsed), **Then** the task resumes execution from its saved state.
3. **Given** a task is in a wait state, **When** a configurable timeout expires without the condition being met, **Then** the task is marked as timed out and an error is logged.

---

### Edge Cases

- What happens when the daemon is started while another instance is already running? The system must detect the existing process and refuse to start a duplicate, reporting a clear error.
- What happens when the system clock changes significantly (e.g., NTP correction, timezone change) during daemon operation? Scheduled jobs should re-evaluate their next run time based on the corrected clock.
- What happens when a cron job's target recipe or prompt reference no longer exists? The job execution should fail gracefully with a descriptive error, and the job should remain registered (not silently deleted).
- What happens when the daemon is stopped while a cron job is mid-execution? The in-progress job should be allowed to complete or be terminated cleanly with logged status.
- What happens when the dreaming pipeline runs but the memory file is locked or inaccessible? The pipeline should retry once, and if still failing, log the error and defer to the next eligible window.
- What happens when hundreds of cron jobs are registered? The scheduler must handle them without degrading heartbeat tick performance.

## Requirements

### Functional Requirements

- **FR-001**: System MUST run as a persistent background daemon that stays alive until explicitly stopped by the user.
- **FR-002**: System MUST execute a heartbeat tick at a configurable interval (default 5 minutes) to monitor and record agent status.
- **FR-003**: System MUST write heartbeat status entries including timestamp, dirty-state flag, and pending task count to a structured status file.
- **FR-004**: System MUST flush accumulated in-memory data to the local database only when a dirty state is detected during a heartbeat tick.
- **FR-005**: System MUST prevent multiple daemon instances from running simultaneously via a process lock mechanism.
- **FR-006**: System MUST support creating cron jobs with a name, cron schedule expression, and target action (prompt text or recipe reference).
- **FR-007**: System MUST support listing all cron jobs with their name, schedule, target, last run, next run, and enabled status.
- **FR-008**: System MUST support updating an existing cron job's schedule, target action, or enabled status.
- **FR-009**: System MUST support deleting a cron job by name.
- **FR-010**: System MUST persist all cron job definitions so they survive daemon restarts.
- **FR-011**: System MUST execute cron job target actions at their scheduled times and log execution results.
- **FR-012**: System MUST automatically trigger the dreaming pipeline when both conditions are satisfied: (a) last user input was more than 2 hours ago, and (b) the current time has passed the configured overnight hour (default 03:00).
- **FR-013**: System MUST record the last dreaming execution timestamp and prevent duplicate dreaming runs within the same calendar day.
- **FR-014**: System MUST support pausing a task into a serialized wait state and resuming it when a specified condition is met or a timeout expires.
- **FR-015**: System MUST log all daemon lifecycle events (start, stop, errors, tick execution) to the execution log.
- **FR-016**: System MUST handle tick and job execution errors gracefully without crashing the daemon process.

### Key Entities

- **Daemon**: The persistent background process that hosts the event loop, heartbeat ticker, and scheduler. Attributes: process ID, start time, status (running/stopped), lock file path.
- **HeartbeatTick**: A single status snapshot captured during a tick cycle. Attributes: timestamp, dirty flag, pending task count, flush performed flag.
- **CronJob**: A user-defined scheduled task. Attributes: unique name, cron expression, target action type (prompt/recipe), target reference, enabled flag, last run timestamp, next run timestamp, creation time.
- **CronJobExecution**: A record of a single cron job run. Attributes: job name, start time, end time, status (success/failure), result summary, error details.
- **DreamingTrigger**: The condition evaluator for automatic dreaming. Attributes: last user input timestamp, last dreaming timestamp, overnight hour setting, idle threshold setting.
- **WaitState**: A serialized paused task. Attributes: task ID, serialized state, wait condition type, registered time, timeout duration, resume callback reference.

## Success Criteria

### Measurable Outcomes

- **SC-001**: The daemon remains operational for 24+ hours continuously without memory leaks or crashes under normal operation.
- **SC-002**: Heartbeat ticks execute within 1 second of their scheduled time under normal system load.
- **SC-003**: Users can create, list, update, and delete cron jobs in under 2 seconds per operation.
- **SC-004**: Cron jobs execute within 5 seconds of their scheduled time.
- **SC-005**: The dreaming pipeline triggers automatically within the first eligible heartbeat tick after both conditions (2-hour idle + overnight hour) are met.
- **SC-006**: The system supports at least 100 concurrent cron jobs without degrading heartbeat performance.
- **SC-007**: All daemon operations (start, stop, tick, job execution) produce auditable log entries.
- **SC-008**: Paused tasks resume within 5 seconds of their resume condition being triggered.

## Assumptions

- The daemon runs on a single machine and does not need distributed coordination or clustering.
- The host system has a stable system clock; minor NTP adjustments are handled gracefully, but large manual clock changes are not a primary concern.
- The existing `ConversationStore` and `DreamingPipeline` modules from Phase 2 are available and functional for dreaming integration.
- The existing `RecipeExecutor` from Phase 2 is available for cron job recipe execution.
- Cron schedule expressions follow standard 5-field cron syntax (minute, hour, day-of-month, month, day-of-week).
- The daemon process is started and stopped via CLI commands; system-level service management (systemd, launchd) integration is out of scope for this feature.
- The configurable heartbeat interval, overnight dreaming hour, and idle threshold are read from `config.yaml`.
- Wait state serialization uses simple JSON-based persistence; complex continuation-passing is out of scope.
