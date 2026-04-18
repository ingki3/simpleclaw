# Tasks: Sub-Agent Dynamic Spawner

**Input**: Design documents from `/specs/007-sub-agent-spawner/`

## Phase 1: Setup

- [x] T001 Add sub_agents configuration section to config.yaml
- [x] T002 Extend config loader with `load_sub_agents_config()` in src/simpleclaw/config.py
- [x] T003 Create agents package directory and __init__.py in src/simpleclaw/agents/__init__.py

---

## Phase 2: Foundational

- [x] T004 [P] Create agent models (SubAgentStatus, PermissionScope, SubAgent, SubAgentResult, SpawnRequest, error classes) in src/simpleclaw/agents/models.py
- [x] T005 [P] Implement WorkspaceManager (create/cleanup sandboxed directories) in src/simpleclaw/agents/workspace.py
- [x] T006 Implement ConcurrencyPool (asyncio.Semaphore wrapper with running/queued counts) in src/simpleclaw/agents/pool.py

---

## Phase 3: User Story 1 — Spawn and Execute (Priority: P1) MVP

- [x] T007 [US1] Implement SubAgentSpawner with spawn() method (asyncio.create_subprocess_exec, JSON stdout parsing, timeout, error handling) in src/simpleclaw/agents/spawner.py
- [x] T008 [US1] Add spawn_python() convenience method and get_running() to SubAgentSpawner in src/simpleclaw/agents/spawner.py
- [x] T009 [US1] Export public API in src/simpleclaw/agents/__init__.py

---

## Phase 4: User Story 2 — Concurrency Pool (Priority: P1)

- [x] T010 [US2] Integrate ConcurrencyPool into SubAgentSpawner (acquire before spawn, release on completion/error/timeout) in src/simpleclaw/agents/spawner.py
- [x] T011 [US2] Add get_pool_status() method to SubAgentSpawner in src/simpleclaw/agents/spawner.py

---

## Phase 5: User Story 3 — Permission Scope (Priority: P1)

- [x] T012 [US3] Implement permission scope injection via AGENT_SCOPE environment variable in SubAgentSpawner.spawn() in src/simpleclaw/agents/spawner.py
- [x] T013 [US3] Apply default restrictive scope when none provided in src/simpleclaw/agents/spawner.py

---

## Phase 6: User Story 4 — Sandboxed Workspace (Priority: P2)

- [x] T014 [US4] Integrate WorkspaceManager into SubAgentSpawner (create workspace before spawn, include in scope) in src/simpleclaw/agents/spawner.py
- [x] T015 [US4] Add shutdown() method for graceful termination of all running sub-agents in src/simpleclaw/agents/spawner.py

---

## Phase 7: Polish & Tests

- [x] T016 [P] Write unit tests for ConcurrencyPool in tests/unit/test_agent_pool.py
- [x] T017 [P] Write unit tests for WorkspaceManager in tests/unit/test_agent_workspace.py
- [x] T018 [P] Write unit tests for SubAgentSpawner in tests/unit/test_agent_spawner.py
- [x] T019 Write integration test for sub-agent pipeline in tests/integration/test_agent_pipeline.py
- [x] T020 Run full test suite and fix any failures
