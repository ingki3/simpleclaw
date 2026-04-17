# Tasks: 스킬 로더 및 MCP 클라이언트

## Phase 1: Setup

- [x] T001 Create directory structure: `src/simpleclaw/skills/`, `tests/fixtures/skills/test-skill/`, `tests/fixtures/skills/another-skill/`
- [x] T002 [P] Update `pyproject.toml` to add dependency: `mcp`
- [x] T003 [P] Update `config.yaml` to add skills and mcp sections

---

## Phase 2: Foundational

- [x] T004 Define data classes in `src/simpleclaw/skills/models.py`: SkillDefinition, SkillResult, ToolDefinition, SkillScope enum (LOCAL/GLOBAL), ToolSource enum (SKILL/MCP), error classes
- [x] T005 [P] Create test fixture skills: `tests/fixtures/skills/test-skill/SKILL.md` + `run.py`, `tests/fixtures/skills/another-skill/SKILL.md` + `run.sh`

---

## Phase 3: US1 — 스킬 디스커버리 (P1) 🎯 MVP

- [x] T006 [US1] Implement skill discovery in `src/simpleclaw/skills/discovery.py`: discover_skills(local_dir, global_dir) that scans directories, parses SKILL.md, applies local-override, returns list[SkillDefinition]
- [x] T007 [US1] Write unit tests in `tests/unit/test_skill_discovery.py`: test discovery with local/global/both/empty, test local override, test missing SKILL.md skip

---

## Phase 4: US2 — 스킬 실행 (P2)

- [x] T008 [US2] Implement skill executor in `src/simpleclaw/skills/executor.py`: async execute_skill(skill, args) that runs target script via asyncio subprocess, returns SkillResult with stdout/stderr/exit_code, handles timeout
- [x] T009 [US2] Write unit tests in `tests/unit/test_skill_executor.py`: test successful execution, test with args, test script not found, test non-zero exit, test timeout

---

## Phase 5: US3 — MCP 클라이언트 (P3)

- [x] T010 [US3] Implement MCP client in `src/simpleclaw/skills/mcp_client.py`: MCPManager that connects to configured MCP servers, lists tools, calls tools, handles connection failures gracefully
- [x] T011 [US3] Write unit tests in `tests/unit/test_mcp_client.py`: test tool listing with mock server, test tool call, test connection failure graceful handling

---

## Phase 6: Polish

- [x] T012 Export public API in `src/simpleclaw/skills/__init__.py`
- [x] T013 [P] Add `list_all_tools()` function that combines skills and MCP tools into unified ToolDefinition list
