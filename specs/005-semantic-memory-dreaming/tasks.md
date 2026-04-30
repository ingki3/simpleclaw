# Tasks: Semantic Memory & RAG-based Recall

**Input**: Design documents from `/specs/005-semantic-memory-dreaming/`
**Prerequisites**: spec.md, plan.md

## Phase 1 — Storage Layer (PR ingki3/simpleclaw#16)

- [x] T001 `pyproject.toml` 의존성 추가: `numpy>=1.26`, `sqlite-vec>=0.1`
- [x] T002 `ConversationStore._ensure_schema()` 확장 (embedding 컬럼 + ALTER 마이그레이션 + WAL)
- [x] T003 `ConversationStore.add_message()` 시그니처 확장: `-> None` → `-> int`
- [x] T004 `ConversationStore.add_embedding(message_id, vector)` 신규
- [x] T005 `ConversationStore.search_similar(query_vector, k, since)` 신규
- [x] T006 `tests/unit/test_conversation_store_vector.py` 19 tests
- [x] T007 `TODO.md` 백로그 등록
- [x] T008 `pytest tests/unit/` 전체 통과 (442/442 시점)
- [x] T009 `ruff check` 변경 파일 통과

## Phase 2 — Retrieval Integration (이 PR 범위)

- [x] P2-T001 `sentence-transformers>=3.0` 의존성 추가
- [x] P2-T002 `EmbeddingService` 신규 모듈 — 모델 lazy 로드, `encode_query` / `encode_passage` (e5 프리픽스), 실패 시 graceful disable
- [x] P2-T003 `Orchestrator._save_turn` + `_schedule_embedding` — `asyncio.to_thread`로 워커 스레드에서 임베딩, fire-and-forget 백그라운드 태스크
- [x] P2-T004 `_retrieve_relevant_context(user_text, exclude_contents)` — 임계값 필터 + 최근 윈도우 중복 제거 + "관련 과거 대화" 마크다운 블록 포맷
- [x] P2-T005 `_tool_loop()`에서 isolated가 아닐 때만 RAG 호출, `_build_system_prompt(rag_context=)`로 시스템 프롬프트에 주입
- [x] P2-T006 `config.yaml.example`에 `memory.rag.{enabled,model,top_k,similarity_threshold}` 노출, `load_memory_config()` 추가 (기본 enabled=False)
- [x] P2-T007 단위 테스트 — `test_embedding_service.py` 9 tests + `test_orchestrator_rag.py` 12 tests. 전체 463 tests pass.

## Phase 3 — Graph-style Dreaming (별도 PR)

- [ ] P3-T001 드리밍 입력을 임베딩 클러스터 기준으로 그룹핑
- [ ] P3-T002 클러스터별 요약 갱신(append → upsert)으로 `MEMORY.md` 자동 압축
- [ ] P3-T003 시맨틱 ↔ 에피소드 분리 인덱스 도입 (테이블 분리 또는 태그 컬럼)
