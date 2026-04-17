# Tasks: 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑

**Input**: Design documents from `/specs/002-llm-router-cli-wrapper/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Phase 1: Setup

- [x] T001 Create directory structure: `src/simpleclaw/llm/providers/`
- [x] T002 [P] Update `pyproject.toml` to add dependencies: anthropic, openai, google-genai, python-dotenv, pytest-asyncio (dev)
- [x] T003 [P] Update `config.yaml` to add llm section with default provider and providers list

---

## Phase 2: Foundational

- [x] T004 Define data classes in `src/simpleclaw/llm/models.py`: BackendType enum, LLMBackend, LLMRequest, LLMResponse dataclasses, and error classes (LLMConfigError, LLMAuthError, LLMProviderError, LLMTimeoutError, LLMCLINotFoundError)
- [x] T005 [P] Extend `src/simpleclaw/config.py`: add load_llm_config(config_path) that reads config.yaml llm section and .env for API keys

---

## Phase 3: User Story 1 — 설정 기반 LLM 모델 선택 및 메시지 전송 (P1) 🎯 MVP

### Implementation

- [x] T006 [US1] Implement abstract base provider in `src/simpleclaw/llm/providers/base.py`: LLMProvider ABC with async send(system_prompt, user_message) method
- [x] T007 [P] [US1] Implement Claude provider in `src/simpleclaw/llm/providers/claude.py`: uses anthropic SDK, reads API key from env var
- [x] T008 [P] [US1] Implement OpenAI provider in `src/simpleclaw/llm/providers/openai_provider.py`: uses openai SDK, reads API key from env var
- [x] T009 [P] [US1] Implement Gemini provider in `src/simpleclaw/llm/providers/gemini.py`: uses google-genai SDK, reads API key from env var
- [x] T010 [US1] Implement router in `src/simpleclaw/llm/router.py`: create_router(config_path), LLMRouter class with async send(request), list_backends(), get_default_backend()
- [x] T011 [US1] Write unit tests in `tests/unit/test_providers.py`: mock SDK calls, test each provider returns LLMResponse, test auth error, test provider error
- [x] T012 [US1] Write unit tests in `tests/unit/test_router.py`: test default backend selection, test explicit backend selection, test unknown backend error, test list_backends

---

## Phase 4: User Story 2 — 외부 CLI 도구 서브프로세스 래핑 (P2)

### Implementation

- [x] T013 [US2] Implement CLI wrapper in `src/simpleclaw/llm/cli_wrapper.py`: CLIProvider extending LLMProvider, uses asyncio.create_subprocess_exec, handles timeout/not-found/stderr
- [x] T014 [US2] Write unit tests in `tests/unit/test_cli_wrapper.py`: test successful CLI call (mock with echo), test timeout, test CLI not found, test non-zero exit code

---

## Phase 5: User Story 3 — 페르소나 프롬프트 통합 (P3)

### Implementation

- [x] T015 [US3] Write integration test in `tests/integration/test_llm_pipeline.py`: end-to-end test that loads persona files, assembles prompt, creates router with mock provider, sends request, verifies system_prompt contains persona content

---

## Phase 6: Polish

- [x] T016 Export public API in `src/simpleclaw/llm/__init__.py`: re-export create_router, LLMRouter, LLMRequest, LLMResponse, all error classes
- [x] T017 [P] Export providers in `src/simpleclaw/llm/providers/__init__.py`

---

## Dependencies

- Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
- T007, T008, T009 can run in parallel (independent providers)
- T002, T003 can run in parallel
