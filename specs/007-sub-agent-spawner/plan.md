# Implementation Plan: Sub-Agent Dynamic Spawner

**Branch**: `007-sub-agent-spawner` | **Date**: 2026-04-18 | **Spec**: [spec.md](./spec.md)

## Summary

Implement a sub-agent spawning system using `asyncio.create_subprocess_exec` with JSON-over-Stdout communication, concurrency pool limiting (default 3), permission scope injection via environment variable, and sandboxed workspace directories.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: None new (uses stdlib asyncio, json, uuid)
**Storage**: Filesystem (workspace directories)
**Testing**: pytest + pytest-asyncio
**Target Platform**: macOS / Linux
**Project Type**: Library module
**Constraints**: Max 3 concurrent sub-agents (configurable), cooperative permission enforcement

## Constitution Check

| Principle | Status |
|-----------|--------|
| I. Python-Only Core | PASS |
| II. Lightweight Dependencies | PASS — no new dependencies |
| III. Configuration-Driven Flexibility | PASS — config.yaml controls limits, timeouts, scope |
| IV. Explicit Security & Permission Scope | PASS — permission scope injected at spawn time |
| V. Test-After Implementation | PASS |
| VI. Persona & Memory Integrity | N/A |
| VII. Extensibility via Isolation | PASS — sub-agents sandboxed in workspace/sub_agents/ |

## Project Structure

```text
src/simpleclaw/
├── agents/
│   ├── __init__.py
│   ├── models.py          # SubAgent, PermissionScope, SpawnRequest, SubAgentResult
│   ├── spawner.py          # SubAgentSpawner: spawn, pool management, lifecycle
│   ├── pool.py             # ConcurrencyPool: semaphore + queue management
│   └── workspace.py        # WorkspaceManager: create/cleanup sandboxed dirs
├── config.py               # (existing) — extend with sub_agents config

tests/
├── unit/
│   ├── test_agent_spawner.py
│   ├── test_agent_pool.py
│   └── test_agent_workspace.py
└── integration/
    └── test_agent_pipeline.py
```
