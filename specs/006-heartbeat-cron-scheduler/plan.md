# Implementation Plan: Heartbeat Daemon & Cron Scheduler

**Branch**: `006-heartbeat-cron-scheduler` | **Date**: 2026-04-18 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-heartbeat-cron-scheduler/spec.md`

## Summary

Implement a persistent background daemon using `asyncio` + `APScheduler` that provides: (1) periodic heartbeat monitoring with dirty-state-aware database flushing, (2) full CRUD cron job management with SQLite persistence, (3) automatic dreaming pipeline triggering based on idle-time + overnight-hour conditions, and (4) async wait state support for paused tasks.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `apscheduler>=3.10` (scheduling), existing `simpleclaw` modules (memory, recipes)
**Storage**: SQLite (cron jobs, execution logs, wait states) via existing patterns + HEARTBEAT.md status file
**Testing**: pytest + pytest-asyncio
**Target Platform**: macOS / Linux (single machine)
**Project Type**: CLI daemon (background process)
**Performance Goals**: Heartbeat ticks within 1s of schedule, cron jobs within 5s, 100+ concurrent jobs
**Constraints**: Single-instance via PID lock file, <50MB memory footprint
**Scale/Scope**: Single user, single machine, ~100 cron jobs max

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Python-Only Core | PASS | All daemon, scheduler, and heartbeat code in Python |
| II. Lightweight Dependencies | PASS | Using APScheduler (lightweight), SQLite, no Celery/Docker |
| III. Configuration-Driven Flexibility | PASS | Heartbeat interval, dreaming hour, idle threshold all from config.yaml |
| IV. Explicit Security & Permission Scope | PASS | No external access points in this feature; daemon is local-only |
| V. Test-After Implementation | PASS | Will test after implementation |
| VI. Persona & Memory Integrity | PASS | Dreaming trigger reuses existing DreamingPipeline with .bak backup |
| VII. Extensibility via Isolation | PASS | Daemon module isolated in `src/simpleclaw/daemon/` |

All gates pass. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/006-heartbeat-cron-scheduler/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/simpleclaw/
├── daemon/
│   ├── __init__.py          # Public API exports
│   ├── models.py            # Dataclasses: CronJob, HeartbeatTick, WaitState, etc.
│   ├── heartbeat.py         # HeartbeatMonitor: tick logic, dirty-state detection, HEARTBEAT.md writes
│   ├── scheduler.py         # CronScheduler: APScheduler wrapper, CRUD for cron jobs
│   ├── dreaming_trigger.py  # DreamingTrigger: condition evaluator, auto-trigger logic
│   ├── wait_states.py       # WaitStateManager: serialize/resume paused tasks
│   ├── daemon.py            # AgentDaemon: main orchestrator, startup/shutdown, PID lock
│   └── store.py             # DaemonStore: SQLite persistence for cron jobs, executions, wait states
├── config.py                # (existing) — extend with daemon config section
├── memory/                  # (existing) — DreamingPipeline, ConversationStore
├── recipes/                 # (existing) — RecipeExecutor for cron job targets
└── ...

tests/
├── unit/
│   ├── test_heartbeat.py
│   ├── test_scheduler.py
│   ├── test_dreaming_trigger.py
│   ├── test_wait_states.py
│   ├── test_daemon.py
│   └── test_daemon_store.py
└── integration/
    └── test_daemon_pipeline.py
```

**Structure Decision**: New `daemon/` package under existing `src/simpleclaw/` following the established modular pattern (persona/, llm/, memory/, skills/, recipes/).

## Complexity Tracking

> No violations to justify. All gates pass.
