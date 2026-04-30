# Feature Specification: Semantic Memory & RAG-based Recall

**Feature Branch**: `005-semantic-memory-dreaming`
**Created**: 2026-04-30
**Status**: Phase 1 In Progress
**Source Issue**: Multica BIZ-12 — "Vector DB / RAG 시맨틱 메모리"
**Input**: 현재 SimpleClaw 메모리는 "최근 N개 + 누적 요약 텍스트(MEMORY.md)" 라는 시간순 슬라이딩 윈도우 구조라 시간 경계 밖 회상이 구조적으로 불가능. SQLite 단일 파일 운영을 유지한 채 임베딩 기반 시맨틱 검색을 도입하여 (1) 무한 시간 회상, (2) 호출당 프롬프트 토큰 절감, (3) 교차 대화 연결을 가능케 한다.

## User Scenarios & Testing

### User Story 1 — 시간 경계를 넘은 구체 사실 회상 (Priority: P1)

사용자로서, 며칠 또는 몇 주 전에 나눴던 구체적인 대화(가격, 일정, 이름 등)를 다시 묻고 싶다. 현재는 최근 20개 메시지 안에 없으면 USER.md의 일반화된 요약("맥북에 관심 있음")만 남아 있어 구체 숫자/날짜 복원이 불가능하다.

**Why this priority**: PRD §3.1, §4.2의 핵심 약속. 회상 실패는 사용자가 "이 에이전트는 기억을 못 한다"고 체감하는 가장 큰 불만 지점이다.

**Independent Test**: 2주 전 대화에 "맥북 가격 240만원" 같은 구체 사실을 심어두고, "지난번 맥북 가격 얼마였지?" 질문에 해당 메시지가 RAG로 회수되어 응답에 반영되는지 검증한다.

**Acceptance Scenarios**:

1. **Given** N>50개의 메시지가 누적되어 최근 윈도우 밖에 특정 사실이 있을 때, **When** 사용자가 그 사실을 가리키는 질문을 하면, **Then** 시맨틱 검색이 해당 메시지를 top-K 안으로 복원하여 시스템 프롬프트에 포함한다.
2. **Given** 임베딩이 아직 부착되지 않은 레거시 메시지가 있을 때, **When** 검색이 실행되면, **Then** 임베딩이 있는 메시지만 후보가 되며 누락은 에러 없이 무시된다.
3. **Given** 동일/유사 의미의 메시지가 다수 존재할 때, **When** 검색이 실행되면, **Then** 코사인 유사도 상위 K개가 시간 가중치(최근 우선) 없이 의미 우선으로 반환된다.

---

### User Story 2 — 프롬프트 토큰 비용 절감 (Priority: P2)

운영자로서, 매 호출마다 누적 요약 파일과 최근 20개 메시지가 통째 주입되는 토큰 낭비를 줄이고 싶다.

**Independent Test**: 동일 시나리오에서 RAG 적용 전/후 호출당 입력 토큰 수를 비교하여 30% 이상 감소하는지 확인한다.

**Acceptance Scenarios**:

1. **Given** RAG가 활성화되었을 때, **When** `_tool_loop()`이 컨텍스트를 조립하면, **Then** `최근 5~10개 + RAG top-K` 하이브리드로 구성되어 기존 `최근 20개 + MEMORY.md 전체`보다 토큰이 적다.
2. **Given** 검색 결과가 비었을 때, **When** 컨텍스트가 조립되면, **Then** 기존 슬라이딩 윈도우 동작으로 fallback 한다(서비스 가용성 보존).

*(Phase 2 범위. Phase 1에서는 저장소 API만 제공한다.)*

---

### User Story 3 — 교차 대화 링크 / 지시어 해소 (Priority: P3)

사용자로서, "그 프로젝트 어떻게 됐어?" 같은 지시어 질문에 후보 프로젝트를 회상해 자연스럽게 후속 대화가 이어지길 기대한다.

**Independent Test**: 여러 프로젝트 대화가 누적된 상태에서 지시어 질문을 던져, 가장 유사도 높은 프로젝트 컨텍스트가 회상되는지 검증한다.

*(Phase 3 범위. 드리밍 그래프 갱신과 함께 다룬다.)*

---

## Functional Requirements

### Phase 1 (이 PR 범위)

- **FR-1.1** `ConversationStore`는 메시지 저장 시 그 행 ID를 반환하여 외부에서 임베딩을 사후 부착할 수 있어야 한다.
- **FR-1.2** `ConversationStore`는 `messages` 테이블에 nullable `embedding BLOB` 컬럼을 보유하며, 기존 DB 파일에 대해 자동 마이그레이션을 수행한다(컬럼 누락 시 `ALTER TABLE`).
- **FR-1.3** `ConversationStore.add_embedding(message_id, vector)` — 주어진 메시지에 임베딩을 부착한다. 벡터 차원은 호출자가 일관되게 관리한다(저장소는 차원을 강제하지 않으나 동일 DB 내 mixed-dim은 검색 시 무시한다).
- **FR-1.4** `ConversationStore.search_similar(query_vector, k=5, since=None)` — 코사인 유사도 상위 K개의 `(ConversationMessage, score)`를 반환한다. `since` 인자가 주어지면 timestamp 필터를 함께 적용한다.
- **FR-1.5** SQLite 연결은 WAL 저널 모드로 동작하여 데몬 + dreaming 동시 쓰기 시 잠금 충돌을 줄인다.
- **FR-1.6** 기존 메서드(`add_message`, `get_recent`, `get_since`, `count`)의 시그니처와 의미는 변경되지 않는다(`add_message`는 반환 타입만 `None → int`로 확장; 기존 호출자는 무시 가능).

### Phase 2 (별도 PR)

- **FR-2.1** 임베딩 모델 통합: `intfloat/multilingual-e5-small`을 사용하여 메시지 저장 직후 동기적으로 임베딩을 부착한다.
- **FR-2.2** `_retrieve_relevant_context(user_msg, k)`을 `orchestrator.py`에 신설하고 `_tool_loop()`에서 호출하여 시스템 프롬프트에 RAG 결과를 주입한다.
- **FR-2.3** `최근 N개 + RAG top-K` 하이브리드. 검색 실패/임베딩 부재 시 슬라이딩 윈도우만으로 fallback.

### Phase 3 (별도 PR)

- **FR-3.1** 드리밍 파이프라인이 누적 텍스트가 아닌 임베딩 클러스터 그래프를 갱신하도록 전환.
- **FR-3.2** `MEMORY.md` 자동 압축 — 임베딩 그룹별 요약 1건만 유지.

---

## Out of Scope

- **차원 검증/강제** — Phase 1에서는 저장소가 차원 일관성을 강제하지 않는다(호출자 책임).
- **하이브리드 키워드+벡터 검색** — Phase 2 이후 튜닝 시 검토.
- **그래프 DB(Cognee, Kuzu)** — 단일 SQLite 파일 운영 원칙 유지를 위해 채택하지 않는다(이슈 본문 1순위였으나 Decision Doc D1에 따라 sqlite-vec 라우트로 변경).

## Key Entities

- **Message Row**: `(id, role, content, timestamp, token_count, embedding)` — 기존 행 + nullable BLOB 컬럼 추가.
- **Embedding BLOB**: float32 little-endian 연속 바이트열. `numpy.ndarray.astype(np.float32).tobytes()` 직렬화.
- **Search Result**: `(ConversationMessage, similarity_score: float)` — 코사인 유사도 [-1, 1] 범위.

## Decision Records

- **D1 (2026-04-30)**: 벡터 스택을 `sqlite-vec + sentence-transformers`로 결정. PRD §3.1의 "Cognee 1순위"는 단일 SQLite 파일 운영 원칙과 충돌하여 선택하지 않음. PRD 갱신 필요(별도 작업).
- **D2 (2026-04-30)**: 임베딩 모델 = `intfloat/multilingual-e5-small` (118M, 384dim, 한/영 동시 지원, CPU 50ms).
- **D3 (2026-04-30)**: Phase 1 PR은 저장소 확장만 다루며 `_retrieve_relevant_context()` 통합은 Phase 2 별도 PR에서 진행.
- **D4 (2026-04-30, 구현 노트)**: Phase 1은 BLOB 컬럼 + numpy 코사인 유사도(인메모리)로 구현. `sqlite-vec`는 의존성으로 추가하되 가상 테이블 마이그레이션은 메시지 수 >10k 임계 도달 시 Phase 2에서 검토.
