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

## Phase 2 — Retrieval Integration (PR ingki3/simpleclaw#17)

- [x] P2-T001 `sentence-transformers>=3.0` 의존성 추가
- [x] P2-T002 `EmbeddingService` 신규 모듈 — 모델 lazy 로드, `encode_query` / `encode_passage` (e5 프리픽스), 실패 시 graceful disable
- [x] P2-T003 `Orchestrator._save_turn` + `_schedule_embedding` — `asyncio.to_thread`로 워커 스레드에서 임베딩, fire-and-forget 백그라운드 태스크
- [x] P2-T004 `_retrieve_relevant_context(user_text, exclude_contents)` — 임계값 필터 + 최근 윈도우 중복 제거 + "관련 과거 대화" 마크다운 블록 포맷
- [x] P2-T005 `_tool_loop()`에서 isolated가 아닐 때만 RAG 호출, `_build_system_prompt(rag_context=)`로 시스템 프롬프트에 주입
- [x] P2-T006 `config.yaml.example`에 `memory.rag.{enabled,model,top_k,similarity_threshold}` 노출, `load_memory_config()` 추가 (기본 enabled=False)
- [x] P2-T007 단위 테스트 — `test_embedding_service.py` 9 tests + `test_orchestrator_rag.py` 12 tests. 전체 463 tests pass.

## Phase 3 — Graph-style Dreaming (이 PR 범위)

- [>] P3-T001 `semantic_clusters` 테이블 + `messages.cluster_id` 컬럼 + 마이그레이션(멱등)
- [>] P3-T002 `ConversationStore` 클러스터 CRUD: `create_cluster`, `update_cluster`, `assign_cluster`, `list_clusters`, `get_cluster`, `get_messages_for_cluster`
- [>] P3-T003 `ClusterRecord` 데이터클래스 (`memory/models.py`)
- [>] P3-T004 `IncrementalClusterer` 신규 모듈 — `find_nearest`, `update_centroid` (numpy)
- [>] P3-T005 `DreamingPipeline` 갱신: 클러스터별 그룹핑 → 클러스터당 LLM 요약 → `semantic_clusters` upsert
- [>] P3-T006 MEMORY.md 마커 기반 upsert 헬퍼 (`<!-- cluster:N start --> ... <!-- cluster:N end -->` 영역만 교체)
- [>] P3-T007 단위 테스트: `test_clustering.py`, `test_conversation_store_clusters.py`, `test_dreaming_phase3.py`
- [>] P3-T008 `pytest tests/unit/` 전체 통과
- [>] P3-T009 `ruff check src/simpleclaw/memory/` 통과
- [>] P3-T010 TODO.md 업데이트, PR 생성

## Phase 3 — Backlog (별도 작업)

- [ ] 주기적 전면 re-clustering 잡 (centroid 드리프트 보정)
- [ ] MEMORY.md 사용자 수기 메모 보존 정책 docs 문서화
- [ ] sklearn HDBSCAN 도입 검토 (메시지 ≥ 수만개 시점)
