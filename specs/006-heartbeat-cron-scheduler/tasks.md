# Tasks: Heartbeat Daemon & Cron Scheduler

**Input**: Design documents from `/specs/006-heartbeat-cron-scheduler/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/daemon_api.md, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add APScheduler dependency, create daemon package structure, extend config

- [x] T001 Add `apscheduler>=3.10` to dependencies in pyproject.toml
- [x] T002 Create daemon package directory and __init__.py in src/simpleclaw/daemon/__init__.py
- [x] T003 Add daemon configuration section (heartbeat_interval, pid_file, status_file, dreaming, wait_state) to config.yaml
- [x] T004 Extend config loader with `load_daemon_config()` function in src/simpleclaw/config.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Models, enums, and SQLite store that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 [P] Create daemon models (ActionType, ExecutionStatus enums; CronJob, CronJobExecution, HeartbeatTick, WaitState, DaemonState dataclasses) in src/simpleclaw/daemon/models.py
- [x] T006 Implement DaemonStore with SQLite table creation (cron_jobs, cron_executions, wait_states, daemon_state), full CRUD for all entities in src/simpleclaw/daemon/store.py

**Checkpoint**: Foundation ready — models and persistence layer available for all user stories

---

## Phase 3: User Story 1 — Daemon Startup & Heartbeat Monitoring (Priority: P1) MVP

**Goal**: Persistent background daemon with periodic heartbeat tick that monitors and records agent status in HEARTBEAT.md, with dirty-state-aware database flushing and PID lock single-instance enforcement.

**Independent Test**: Start daemon, wait for multiple ticks, verify HEARTBEAT.md is updated with current timestamps and dirty-state detection. Verify duplicate instance is rejected.

### Implementation for User Story 1

- [x] T007 [US1] Implement HeartbeatMonitor with tick() method (dirty-state detection, HEARTBEAT.md write, conditional DB flush) in src/simpleclaw/daemon/heartbeat.py
- [x] T008 [US1] Implement AgentDaemon with PID lock file management (acquire/release), asyncio event loop setup, APScheduler AsyncIOScheduler initialization, start/stop lifecycle, heartbeat interval trigger in src/simpleclaw/daemon/daemon.py
- [x] T009 [US1] Export public API (AgentDaemon, HeartbeatMonitor, HeartbeatTick) in src/simpleclaw/daemon/__init__.py

**Checkpoint**: Daemon starts, runs heartbeat ticks at configured interval, writes HEARTBEAT.md, prevents duplicate instances, stops cleanly.

---

## Phase 4: User Story 2 — Cron Job Management (Priority: P1)

**Goal**: Full CRUD for cron jobs (create, list, update, delete) with persistent scheduling that survives daemon restarts. Jobs execute prompts or recipes at scheduled times.

**Independent Test**: Create a cron job, list it, update its schedule, verify it persists across daemon restart, delete it.

### Implementation for User Story 2

- [x] T010 [US2] Implement CronScheduler with add_job, list_jobs, get_job, update_job, remove_job, enable_job, disable_job methods and APScheduler CronTrigger integration in src/simpleclaw/daemon/scheduler.py
- [x] T011 [US2] Add cron job execution logic to CronScheduler: execute target action (prompt via LLM router or recipe via RecipeExecutor), log CronJobExecution results in src/simpleclaw/daemon/scheduler.py
- [x] T012 [US2] Integrate CronScheduler into AgentDaemon: load persisted jobs on startup, register with APScheduler, handle graceful shutdown of running jobs in src/simpleclaw/daemon/daemon.py
- [x] T013 [US2] Export CronScheduler, CronJob, ActionType in src/simpleclaw/daemon/__init__.py

**Checkpoint**: Cron jobs can be created, listed, updated, deleted. Jobs execute at scheduled times and persist across restarts.

---

## Phase 5: User Story 3 — Dreaming Pipeline Auto-Trigger (Priority: P2)

**Goal**: Automatically trigger the existing DreamingPipeline when both conditions are met: last user input >2 hours ago AND current time past configured overnight hour (default 03:00).

**Independent Test**: Simulate conversation activity, advance past idle threshold and overnight hour, verify dreaming pipeline runs and records timestamp to prevent same-day re-runs.

### Implementation for User Story 3

- [x] T014 [US3] Implement DreamingTrigger with should_run() condition check (queries ConversationStore for last input, checks overnight hour, checks last dreaming timestamp via DaemonStore) and execute() method in src/simpleclaw/daemon/dreaming_trigger.py
- [x] T015 [US3] Integrate DreamingTrigger into HeartbeatMonitor tick cycle: call should_run() on each tick, execute dreaming if conditions met in src/simpleclaw/daemon/heartbeat.py
- [x] T016 [US3] Export DreamingTrigger in src/simpleclaw/daemon/__init__.py

**Checkpoint**: Dreaming pipeline auto-triggers when idle + overnight conditions are met, prevents same-day duplicates.

---

## Phase 6: User Story 4 — Async Wait States (Priority: P3)

**Goal**: Support pausing tasks into serialized wait states and resuming them when conditions are met or timeouts expire.

**Independent Test**: Register a wait state, verify it persists, resolve it, verify timed-out states are detected.

### Implementation for User Story 4

- [x] T017 [US4] Implement WaitStateManager with register_wait, resolve_wait, get_pending, check_timeouts methods in src/simpleclaw/daemon/wait_states.py
- [x] T018 [US4] Integrate WaitStateManager timeout checking into HeartbeatMonitor tick cycle in src/simpleclaw/daemon/heartbeat.py
- [x] T019 [US4] Export WaitStateManager, WaitState in src/simpleclaw/daemon/__init__.py

**Checkpoint**: Wait states can be registered, resolved, and timed out. Timeout checking runs on each heartbeat tick.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Testing, validation, and integration verification

- [x] T020 [P] Write unit tests for DaemonStore (CRUD operations, schema creation) in tests/unit/test_daemon_store.py
- [x] T021 [P] Write unit tests for HeartbeatMonitor (tick logic, dirty-state, HEARTBEAT.md write) in tests/unit/test_heartbeat.py
- [x] T022 [P] Write unit tests for CronScheduler (add/list/update/delete jobs) in tests/unit/test_scheduler.py
- [x] T023 [P] Write unit tests for DreamingTrigger (condition evaluation, same-day prevention) in tests/unit/test_dreaming_trigger.py
- [x] T024 [P] Write unit tests for WaitStateManager (register/resolve/timeout) in tests/unit/test_wait_states.py
- [x] T025 Write integration test for daemon lifecycle (start, tick, cron execution, stop) in tests/integration/test_daemon_pipeline.py
- [x] T026 Run full test suite and fix any failures
- [x] T027 Run quickstart.md scenarios validation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — core daemon
- **US2 (Phase 4)**: Depends on US1 (needs running daemon to schedule jobs)
- **US3 (Phase 5)**: Depends on US1 (needs heartbeat tick cycle for trigger evaluation)
- **US4 (Phase 6)**: Depends on US1 (needs heartbeat tick cycle for timeout checking)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Foundation only — no dependencies on other stories
- **US2 (P1)**: Depends on US1 (daemon must be running for cron execution)
- **US3 (P2)**: Depends on US1 (dreaming trigger evaluated in heartbeat tick)
- **US4 (P3)**: Depends on US1 (timeout check in heartbeat tick)

### Within Each User Story

- Models before services
- Services before integration into daemon
- Core implementation before exports

### Parallel Opportunities

- T005 models can run in parallel with T003/T004 config tasks
- T020-T024 test tasks can all run in parallel
- US3 and US4 can run in parallel after US1 is complete (both integrate into heartbeat tick but touch different concerns)

---

## Parallel Example: User Story 1

```bash
# After Phase 2 is complete, US1 tasks are sequential:
Task T007: HeartbeatMonitor (needs models from T005, store from T006)
Task T008: AgentDaemon (needs HeartbeatMonitor from T007)
Task T009: Exports (needs T007, T008)
```

## Parallel Example: Polish Phase

```bash
# All test tasks can run in parallel:
Task T020: test_daemon_store.py
Task T021: test_heartbeat.py
Task T022: test_scheduler.py
Task T023: test_dreaming_trigger.py
Task T024: test_wait_states.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T004)
2. Complete Phase 2: Foundational (T005-T006)
3. Complete Phase 3: User Story 1 (T007-T009)
4. **STOP and VALIDATE**: Daemon starts, ticks, writes HEARTBEAT.md, prevents duplicates
5. Ready for basic daemon operation

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → Daemon + heartbeat (MVP!)
3. Add US2 → Cron job scheduling
4. Add US3 → Automatic dreaming
5. Add US4 → Wait states
6. Polish → Tests + validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- APScheduler `AsyncIOScheduler` is the runtime; our `DaemonStore` owns persistence
- Existing modules used: `ConversationStore`, `DreamingPipeline`, `RecipeExecutor`
- Config extension adds `daemon:` section to existing `config.yaml`
