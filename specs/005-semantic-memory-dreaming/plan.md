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

- **Phase 1 (PR #16, merged)**: 저장소 확장 + 단위 테스트 + 의존성. 동작 변경 없음.
- **Phase 2 (PR #17, merged into phase1 branch)**: `sentence-transformers` 추가, 메시지 저장 시점 임베딩 부착, `_retrieve_relevant_context()` + `_tool_loop()` 통합. 단, PR #17은 dev가 아닌 phase1 브랜치를 base로 머지되어 Phase 3 PR에 Phase 2 변경분이 함께 포함되는 형태로 dev에 도달함.
- **Phase 3 (이 PR)**: 드리밍 클러스터 그래프 갱신, MEMORY.md 자동 압축, 시맨틱/에피소드 인덱스 분리(`semantic_clusters` 테이블).

## Phase 3 — Architecture

### Schema Additions

```sql
CREATE TABLE IF NOT EXISTS semantic_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,                  -- 사람이 읽을 짧은 라벨 ("맥북 구매 논의")
    centroid BLOB NOT NULL,      -- float32 평균 벡터
    summary TEXT DEFAULT '',     -- LLM 누적 요약 (rolling)
    member_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

ALTER TABLE messages ADD COLUMN cluster_id INTEGER;  -- 마이그레이션 멱등
```

### IncrementalClusterer (numpy 기반)

```python
class IncrementalClusterer:
    def __init__(self, threshold: float = 0.75): ...
    def find_nearest(self, vec, clusters) -> tuple[ClusterRecord | None, float]: ...
    def update_centroid(self, old_centroid, old_count, new_vec) -> np.ndarray:
        """incremental mean: (old * n + new) / (n + 1) — 단위 정규화는 검색 시 수행"""
```

### Dreaming Pipeline 변화

기존 `run()`:
1. 미처리 메시지 수집 → LLM에 전체 텍스트 → 단일 요약 → MEMORY.md append

신규 `run()`:
1. 미처리 메시지 수집
2. 각 메시지 임베딩 조회 → `IncrementalClusterer`로 클러스터 할당(기존 또는 신규)
3. 영향받은 클러스터 ID 집합 결정 → 클러스터별로 **(기존 summary + 신규 메시지)** 를 LLM에 전달, 새 summary 산출
4. `semantic_clusters.summary`/`label`/`updated_at` upsert
5. MEMORY.md 마커 영역만 교체 (`<!-- cluster:N start -->` … `<!-- cluster:N end -->`)
6. 클러스터화 실패 시 기존 append 동작으로 fallback (하위호환)

### MEMORY.md 형식

```markdown
# Memory

(사용자 수기 메모는 마커 외부에 자유롭게 작성 가능)

<!-- cluster:1 start -->
## 맥북 구매 (cluster 1)

- 2026-04-15: 14인치 M3 검토, 240만원 (Apple Care+ 별도)
- 2026-04-22: "비싸지만 구매 의향" 표명
<!-- cluster:1 end -->

<!-- cluster:2 start -->
## 한국시리즈 관전
...
<!-- cluster:2 end -->
```

### Edge Cases

| 상황 | 동작 |
|------|------|
| 미처리 메시지에 임베딩이 전혀 없음(RAG 비활성) | 기존 append 폴백 |
| 임베딩은 있으나 모든 클러스터가 임계값 미달 | 신규 클러스터 N개 생성 |
| 클러스터 centroid와 신규 메시지 차원 불일치 | 해당 메시지 스킵, 로그 경고 |
| MEMORY.md에 마커 없음(레거시 파일) | 새 클러스터 섹션을 파일 끝에 append, 다음 회차부터 upsert |
| 사용자가 마커 내부 본문을 편집 | 다음 드리밍에 LLM이 재작성(경고 없음, 의도된 동작) |
| LLM 호출 실패 | summary 갱신만 스킵, 클러스터 멤버십은 유지 |

## Project Structure (Phase 3 변경 파일)

```
src/simpleclaw/memory/
├── conversation_store.py     # +cluster CRUD, +schema migration
├── clustering.py             # 신규: IncrementalClusterer
├── dreaming.py               # cluster 기반 요약 + MEMORY.md 마커 upsert
└── models.py                 # +ClusterRecord 데이터클래스

tests/unit/
├── test_clustering.py                  # 신규
├── test_conversation_store_clusters.py # 신규
└── test_dreaming_phase3.py             # 신규
```

## Risks (Phase 3)

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 기존 DB 마이그레이션 실패 | 봇 기동 불가 | `ALTER TABLE` + `CREATE TABLE IF NOT EXISTS` 멱등, 단위 테스트로 검증 |
| 클러스터 centroid 드리프트(품질 저하) | 시간 흐름에 따라 클러스터가 모호해짐 | 백로그: 주기적 전면 re-cluster 잡 |
| 사용자가 마커 내부 메모를 잃음 | 사용자 신뢰 하락 | spec/문서로 "마커 외부 영역만 안전" 명시 |
| LLM upsert 비용 증가 | 매 드리밍 호출 수↑(클러스터 수만큼) | 영향받은 클러스터만 갱신, 기존 클러스터 요약 그대로 유지 |
