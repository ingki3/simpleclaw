# Tasks: Semantic Memory & RAG-based Recall

**Input**: Design documents from `/specs/005-semantic-memory-dreaming/`
**Prerequisites**: spec.md, plan.md

## Phase 1 — Storage Layer (이 PR 범위)

- [>] T001 `pyproject.toml` 의존성 추가: `numpy>=1.26`, `sqlite-vec>=0.1`
- [>] T002 `ConversationStore._ensure_schema()` 확장:
  - 신규 DB는 `embedding BLOB` 컬럼 포함하여 생성
  - 기존 DB는 `PRAGMA table_info` 검사 후 `ALTER TABLE` 마이그레이션
  - `PRAGMA journal_mode=WAL` 설정
- [>] T003 `ConversationStore.add_message()` 시그니처 확장: `-> None` → `-> int` (lastrowid 반환)
- [>] T004 `ConversationStore.add_embedding(message_id, vector)` 신규 메서드 — float32 BLOB 저장, 존재하지 않는 id 시 ValueError
- [>] T005 `ConversationStore.search_similar(query_vector, k, since)` 신규 메서드 — numpy 코사인 유사도 상위 K, 차원 불일치/NULL 행 자동 제외
- [>] T006 `tests/unit/test_conversation_store_vector.py` 작성:
  - 임베딩 저장 후 검색 라운드트립
  - 차원 불일치 행 제외
  - NULL 임베딩 행 제외
  - `since` 시간 필터 결합
  - 0 벡터 입력 → ValueError
  - 존재하지 않는 message_id → ValueError
  - 빈 DB 검색 → `[]`
  - 기존 DB 마이그레이션(레거시 컬럼 셋으로 만든 DB에 신규 인스턴스 연결 시 컬럼 자동 추가)
- [>] T007 `TODO.md` 백로그에 005 시맨틱 메모리 항목 추가 (Phase 2/3 명시)
- [ ] T008 `pytest tests/unit/` 전체 통과
- [ ] T009 `ruff check src/` 통과

## Phase 2 — Retrieval Integration (별도 PR)

- [ ] P2-T001 `sentence-transformers>=3.0` 의존성 추가
- [ ] P2-T002 `EmbeddingService` 신규 모듈 — 모델 lazy 로드, `encode(text) -> np.ndarray`
- [ ] P2-T003 `Orchestrator` 메시지 저장 직후 임베딩 부착(블로킹 또는 비동기)
- [ ] P2-T004 `_retrieve_relevant_context(user_msg, k)` 메서드 — 임베딩 후 `search_similar` 호출, top-K 메시지를 시스템 프롬프트 섹션으로 포맷
- [ ] P2-T005 `_tool_loop()`에서 RAG 호출 통합, 기존 `최근 N개`와 결합
- [ ] P2-T006 `config.yaml`에 임베딩 모델/`k`/유사도 임계값 노출
- [ ] P2-T007 통합 테스트 + 토큰 절감 측정

## Phase 3 — Graph-style Dreaming (별도 PR)

- [ ] P3-T001 드리밍 입력을 임베딩 클러스터 기준으로 그룹핑
- [ ] P3-T002 클러스터별 요약 갱신(append → upsert)으로 `MEMORY.md` 자동 압축
- [ ] P3-T003 시맨틱 ↔ 에피소드 분리 인덱스 도입 (테이블 분리 또는 태그 컬럼)
