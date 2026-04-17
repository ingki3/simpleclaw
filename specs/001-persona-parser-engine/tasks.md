# Tasks: 페르소나 설정 파싱 엔진 및 프롬프트 인젝터

**Input**: Design documents from `/specs/001-persona-parser-engine/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Tests are included as the constitution mandates test-after implementation (Principle V).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup

**Purpose**: Project initialization and package structure

- [x] T001 Create project directory structure: `src/simpleclaw/`, `src/simpleclaw/persona/`, `src/simpleclaw/llm/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/`
- [x] T002 Initialize Python package with `src/simpleclaw/__init__.py` and `src/simpleclaw/persona/__init__.py`
- [x] T003 [P] Create `pyproject.toml` with dependencies: markdown-it-py, tiktoken, pyyaml, pytest (dev)
- [x] T004 [P] Create default `config.yaml` with persona section (token_budget: 4096, local_dir: ".agent", global_dir: "~/.agents/main", files list)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data models and config loader that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Define data classes in `src/simpleclaw/persona/models.py`: FileType enum (AGENT/USER/MEMORY), SourceScope enum (LOCAL/GLOBAL), Section dataclass (level, title, content), PersonaFile dataclass (file_type, source_path, source_scope, sections, raw_content), PromptAssembly dataclass (parts, assembled_text, token_count, token_budget, was_truncated)
- [x] T006 [P] Implement config loader in `src/simpleclaw/config.py`: load_persona_config(config_path) that reads config.yaml persona section with defaults fallback when file/key is missing

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — 페르소나 파일 로드 및 파싱 (Priority: P1) 🎯 MVP

**Goal**: 마크다운 페르소나 파일 3종을 파싱하여 구조화된 데이터(PersonaFile)로 변환

**Independent Test**: 테스트용 마크다운 파일 3종을 파싱 엔진에 전달했을 때, 각 파일의 섹션 제목과 본문이 올바르게 분리되는지 검증

### Implementation for User Story 1

- [x] T007 [P] [US1] Create test fixture files in `tests/fixtures/`: `agent.md` (2+ sections with ##), `user.md` (3+ sections), `memory.md` (2+ sections with mixed heading levels)
- [x] T008 [US1] Implement markdown parser in `src/simpleclaw/persona/parser.py`: parse_markdown(file_path, file_type) that uses markdown-it-py to split by headings into Section objects, returns PersonaFile with sections list; handles empty/missing/non-UTF-8 files gracefully
- [x] T009 [US1] Write unit tests in `tests/unit/test_parser.py`: test normal parsing (3 file types), empty file, non-existent file, non-UTF-8 file, file with no headings, file with nested headings (##, ###)

**Checkpoint**: User Story 1 is fully functional — markdown files can be parsed into structured PersonaFile objects

---

## Phase 4: User Story 2 — System Prompt 자동 조립 및 주입 (Priority: P2)

**Goal**: 파싱된 PersonaFile 목록을 AGENT → USER → MEMORY 순서로 조립하고 토큰 예산 초과 시 MEMORY부터 잘라냄

**Independent Test**: 샘플 PersonaFile 데이터로 assemble_prompt를 호출하여 순서, 구분자, 잘라냄이 올바르게 동작하는지 검증

### Implementation for User Story 2

- [x] T010 [US2] Implement prompt assembler in `src/simpleclaw/persona/assembler.py`: assemble_prompt(persona_files, token_budget) that concatenates PersonaFile contents in AGENT→USER→MEMORY order with section separators, counts tokens via tiktoken, truncates MEMORY content from the end if over budget, returns PromptAssembly
- [x] T011 [US2] Write unit tests in `tests/unit/test_assembler.py`: test full assembly (3 files), partial assembly (1-2 files), empty files list, token budget exactly at limit, token budget exceeded (verify MEMORY truncation), zero-length MEMORY after truncation

**Checkpoint**: User Stories 1 AND 2 work together — files can be parsed and assembled into a token-budgeted System Prompt

---

## Phase 5: User Story 3 — 파일 경로 탐색 규칙 적용 (Priority: P3)

**Goal**: 로컬(`.agent/`)과 전역(`~/.agents/main/`) 경로를 탐색하고 로컬 우선 규칙 적용

**Independent Test**: 로컬과 전역 양쪽에 동일 파일을 배치한 뒤, 로컬 내용이 반환되는지 검증

### Implementation for User Story 3

- [x] T012 [US3] Implement path resolver in `src/simpleclaw/persona/resolver.py`: resolve_persona_files(local_dir, global_dir) that scans both directories for AGENT.md/USER.md/MEMORY.md, applies local-override rule (local wins on same file_type), calls parse_markdown for each found file, returns list[PersonaFile]; handles missing directories gracefully
- [x] T013 [US3] Write unit tests in `tests/unit/test_resolver.py`: test local-only, global-only, both exist (local wins), neither exists (empty list + warning), mixed (some local, some global)

**Checkpoint**: All user stories independently functional — full pipeline from file discovery to parsed data

---

## Phase 6: Integration & Polish

**Purpose**: End-to-end pipeline validation and cross-cutting concerns

- [x] T014 Write integration test in `tests/integration/test_persona_pipeline.py`: end-to-end test that creates temp directories with fixture files, calls resolve_persona_files → assemble_prompt, validates final System Prompt content/ordering/token count
- [x] T015 [P] Export public API in `src/simpleclaw/persona/__init__.py`: re-export resolve_persona_files, parse_markdown, assemble_prompt, load_persona_config, and all model classes
- [x] T016 [P] Create LLM interface stub in `src/simpleclaw/llm/__init__.py`: placeholder class with send_message(system_prompt, user_message) method that accepts PromptAssembly.assembled_text (to be implemented in Phase 1 task 2)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phase 3-5)**: All depend on Foundational phase completion
  - US1 (Phase 3): No dependencies on other stories
  - US2 (Phase 4): Uses PersonaFile from US1 but can be tested independently with mock data
  - US3 (Phase 5): Calls parse_markdown from US1 but can be tested independently with mock parser
- **Polish (Phase 6)**: Depends on all user stories being complete

### Within Each User Story

- Fixture/setup before implementation
- Implementation before tests
- Core implementation before integration

### Parallel Opportunities

- T003 and T004 can run in parallel (different files)
- T005 and T006 can run in parallel (different modules)
- T007 can run in parallel with T005/T006 (fixture files)
- T015 and T016 can run in parallel (different modules)

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (models + config)
3. Complete Phase 3: User Story 1 (parser)
4. **STOP and VALIDATE**: Test parsing independently
5. Proceed to User Story 2 (assembler) and User Story 3 (resolver)

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → Parse markdown files (MVP!)
3. Add US2 → Assemble System Prompt with token budget
4. Add US3 → Multi-path file discovery with local override
5. Integration → Full pipeline validated

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
