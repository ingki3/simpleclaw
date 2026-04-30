# Implementation Plan: Semantic Memory & RAG-based Recall

**Branch**: `005-semantic-memory-dreaming` (Phase 1 PR: `feature/005-semantic-memory-phase1`) | **Date**: 2026-04-30 | **Spec**: [spec.md](./spec.md)

## Summary

`ConversationStore`를 확장하여 메시지 단위 임베딩 저장과 코사인 유사도 검색을 지원한다. Phase 1은 저장소 API와 단위 테스트만을 범위로 하며, 실제 임베딩 생성과 `_tool_loop()` 통합은 Phase 2에서 다룬다.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**:
- `numpy>=1.26` (벡터 연산, 코사인 유사도)
- `sqlite-vec>=0.1` (Phase 2 가상 테이블 마이그레이션 대비 — Phase 1에서는 import만)
- *(Phase 2 추가 예정)* `sentence-transformers>=3.0`

**Storage**: 기존 SQLite `conversation.db`에 `embedding BLOB` 컬럼 추가. WAL 모드.
**Testing**: pytest + numpy-based 검증
**Target Platform**: macOS / Linux
**Performance Goals**:
- 임베딩 부착 < 5ms (저장소 단독, 임베딩 생성 시간 제외)
- search_similar k=5 over 10k 메시지 < 100ms (Phase 1 인메모리 코사인 기준)

**Constraints**: 단일 SQLite 파일 운영. 차원 강제 없음(호출자 책임). 기존 콜러 호환 유지.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Python-Only Core | PASS | 순수 Python + numpy |
| II. Lightweight Dependencies | PASS | numpy는 사실상 표준. sqlite-vec는 ~수MB |
| III. Configuration-Driven Flexibility | N/A | Phase 1은 설정 노출 없음(저장소 API만) |
| IV. Explicit Security & Permission Scope | PASS | 외부 통신 없음, 로컬 SQLite |
| V. Test-After Implementation | PASS | 단위 테스트 동반 |
| VI. Persona & Memory Integrity | PASS | 기존 컬럼/메서드 보존, ALTER TABLE만 추가 |
| VII. Extensibility via Isolation | PASS | `memory/` 패키지 내 변경 |

## Migration Strategy

### 신규 DB
초기화 시 `messages` 테이블에 `embedding BLOB` 컬럼을 포함하여 생성한다.

### 기존 DB
`PRAGMA table_info(messages)` 조회 후 `embedding` 컬럼이 없으면 `ALTER TABLE messages ADD COLUMN embedding BLOB`. 기존 행의 `embedding`은 NULL로 남아 검색에서 자동 제외된다.

### WAL 모드
`PRAGMA journal_mode=WAL`을 `_ensure_schema()` 단계에서 설정한다. WAL은 영구 적용이며 매 연결마다 재설정할 필요는 없으나, 멱등하므로 매번 호출해도 문제 없다.

## API Design

```python
class ConversationStore:
    def add_message(self, message: ConversationMessage) -> int:
        """저장 후 INSERT된 행 id를 반환한다(기존 None → int)."""

    def add_embedding(self, message_id: int, vector: Sequence[float] | np.ndarray) -> None:
        """주어진 메시지에 임베딩을 부착한다. 메시지가 존재하지 않으면 ValueError."""

    def search_similar(
        self,
        query_vector: Sequence[float] | np.ndarray,
        k: int = 5,
        since: datetime | None = None,
    ) -> list[tuple[ConversationMessage, float]]:
        """코사인 유사도 기준 상위 K개를 (메시지, 점수) 튜플로 반환.
        embedding NULL 또는 query와 차원이 다른 행은 제외한다.
        결과가 K보다 적을 수 있다.
        """
```

## Edge Cases & Failure Modes

| 상황 | 동작 |
|------|------|
| 빈 DB에서 search_similar | `[]` 반환 |
| 모든 행의 embedding NULL | `[]` 반환 |
| query 차원 ≠ 저장 차원 | 해당 행 무시(에러 없음) |
| 0 벡터(norm=0) 입력 | ValueError |
| add_embedding(존재하지 않는 id) | ValueError |
| 동시 쓰기 | WAL 모드로 reader-writer 동시성 확보 |

## Project Structure (변경 파일)

```
src/simpleclaw/memory/
├── conversation_store.py    # 확장 (단일 파일, ~150 lines 추가)

tests/unit/
├── test_conversation_store.py         # 기존 유지(시그니처 변경 영향 없음)
└── test_conversation_store_vector.py  # 신규

pyproject.toml               # numpy, sqlite-vec 추가
specs/005-semantic-memory-dreaming/  # 본 디렉터리
```

## Phase Breakdown

- **Phase 1 (이 PR)**: 저장소 확장 + 단위 테스트 + 의존성. 동작 변경 없음(콜러가 새 API를 호출하기 전까지).
- **Phase 2 (별도 PR)**: `sentence-transformers` 추가, 메시지 저장 시점 임베딩 부착, `_retrieve_relevant_context()` + `_tool_loop()` 통합, `최근 N개 + RAG top-K` 하이브리드.
- **Phase 3 (별도 PR)**: 드리밍 그래프 갱신, `MEMORY.md` 자동 압축.

## Risks (Phase 1)

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 기존 DB 마이그레이션 실패 | 사용자 봇 기동 불가 | `ALTER TABLE` 멱등 처리 + 단위 테스트로 검증 |
| 대용량 메시지에서 인메모리 코사인 느림 | 검색 레이턴시↑ | 메시지 ≥10k 시 sqlite-vec 가상 테이블 도입(Phase 2/3) |
| numpy 추가로 인한 설치 시간 | 첫 설치 30s 추가 | 로컬 단일 사용자 도구로 수용 가능 |
