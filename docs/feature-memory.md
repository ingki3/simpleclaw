# 대화 기억 및 드리밍

SimpleClaw는 대화를 SQLite에 저장하고, 임베딩 기반 시맨틱 회상(RAG)으로 과거 맥락을 자동 주입하며, 심야 시간에 LLM 드리밍으로 장기 기억을 형성합니다.

## 대화 저장소

모든 사용자-에이전트 대화는 `.agent/conversations.db`에 저장됩니다.

### 히스토리 주입

LLM 호출 시 최근 대화를 자동으로 포함합니다:

```yaml
agent:
  history_limit: 20    # 최근 20개 메시지를 컨텍스트에 포함
```

### Cron 메시지 격리

Cron job으로 실행되는 메시지는 `process_cron_message()`를 통해 격리 처리됩니다. 자세한 내용은 아래 [Cron 메시지 격리](#cron-메시지-격리) 섹션을 참고하세요.

## 시맨틱 메모리 (RAG)

대화 메시지를 임베딩 벡터로 색인해, 슬라이딩 윈도우 히스토리를 넘어선 과거 맥락을 의미 기반으로 회상합니다. 외부 벡터 DB나 Docker 의존성 없이 SQLite + numpy + `sentence-transformers`만으로 동작합니다.

### Phase 1: 임베딩 저장 API (`ConversationStore`)

`messages` 테이블에 `embedding BLOB` 컬럼을 추가하여 메시지별 벡터를 저장합니다.

- `add_embedding(message_id, vector)` — float32 BLOB으로 직렬화하여 저장
- `search_similar(query_vec, top_k)` — numpy 코사인 유사도로 상위 K개 메시지 반환
- `get_message_with_embedding(message_id)` — 메시지 본문 + 임베딩 동시 조회
- 차원/0벡터 검증, 레거시 DB에 대한 ALTER TABLE 자동 마이그레이션

### Phase 2: 임베딩 서비스와 회상 통합

`EmbeddingService`(`src/simpleclaw/memory/embedding_service.py`)는 `sentence-transformers`의 다국어 모델(`intfloat/multilingual-e5-small`)을 lazy-load하여 텍스트를 벡터로 변환합니다.

오케스트레이터의 `_retrieve_relevant_context()`는 매 사용자 요청마다 다음을 수행합니다:

1. 사용자 메시지를 임베딩하여 `search_similar()`로 의미상 가까운 과거 메시지 회상
2. 회상 결과를 시스템 프롬프트의 메모리 섹션에 주입 (페르소나 + 슬라이딩 히스토리에 더해)
3. 임베딩 모델이 미설치되었거나 호출 실패 시 graceful degradation — RAG만 비활성화되고 일반 대화는 정상 동작

```yaml
memory:
  embeddings:
    enabled: true
    model: "intfloat/multilingual-e5-small"
    top_k: 5                # 회상할 메시지 수
    min_score: 0.5          # 코사인 유사도 임계값
```

### Phase 3: 시맨틱 클러스터와 그래프형 드리밍

자주 등장하는 주제를 자동 그룹화해 클러스터별 요약을 `MEMORY.md`에 따로 보존합니다.

**증분 클러스터링** (`IncrementalClusterer` in `clustering.py`)
- 새 임베딩을 기존 클러스터 centroid와 코사인 비교
- 임계값(기본 0.75) 이상이면 부착, 아니면 새 클러스터 생성
- centroid는 멤버 평균으로 누적 갱신 (incremental mean)

**스토어 확장** (`semantic_clusters` 테이블, `messages.cluster_id` FK)
- `create_cluster / list_clusters / update_cluster / assign_cluster`
- `get_messages_for_cluster` — 클러스터 멤버 시간순 조회
- `get_unclustered_with_embeddings` — 임베딩은 있으나 미클러스터링된 메시지

**드리밍 파이프라인 분기**
- 클러스터별로 LLM에게 요약 요청 → `MEMORY.md`에 HTML 마커로 분리 보존:
  ```html
  <!-- cluster:1 start -->
  ## (label) — 요약 본문
  <!-- cluster:1 end -->
  ```
- 마커가 있는 영역은 다음 드리밍에서 해당 클러스터 요약으로 in-place 갱신, 그 외 영역(에피소드형 메모리)은 기존처럼 append
- `enable_clusters` 플래그로 점진 도입 (BIZ-28부터 dev/main 기본 True, OFF로 두려면 명시적으로 false 지정)

## 드리밍 파이프라인

에이전트가 유휴 상태일 때 대화 히스토리를 LLM으로 요약하여 `.agent/MEMORY.md`와 `.agent/USER.md`에 기록합니다.

### 트리거 조건

- **시각**: `overnight_hour` (기본 03:00)
- **유휴**: `idle_threshold` (기본 7200초, 2시간) 동안 활동 없음

### 동작 방식

1. `MEMORY.md`와 `USER.md`의 `.bak` 백업 파일 자동 생성
2. 마지막 드리밍 이후의 대화를 수집
3. LLM에게 대화 분석 요청 (구조화된 JSON 추출)
4. 추출된 내용을 각각 해당 파일에 추가:
   - **memory** (사실, 이벤트, 결정 사항) → `MEMORY.md`에 추가
   - **user_insights** (선호도, 관심사, 습관) → `USER.md`에 추가
   - **active_projects** (사용자가 현재 집중 중인 프로젝트 카드) → `USER.md`의 `active-projects` 섹션을 in-place 갱신 (BIZ-74)
5. (선택) 시맨틱 클러스터가 활성화되어 있으면 클러스터별 요약을 `MEMORY.md`의 HTML 마커 영역에 in-place 갱신

### 설정

```yaml
daemon:
  dreaming:
    overnight_hour: 3       # 드리밍 실행 시각 (03:00)
    idle_threshold: 7200    # 유휴 판단 기준 (초)
    model: "gemini"         # 드리밍에 사용할 LLM (선택, 미지정 시 기본 LLM 사용)
    enable_clusters: true   # 시맨틱 클러스터 기반 그래프형 드리밍 활성화 (BIZ-28부터 기본 ON)
    cluster_threshold: 0.75 # 코사인 유사도 임계값 — 낮추면 클러스터가 커지고, 높이면 작아진다
    active_projects:
      enabled: true                          # USER.md의 active-projects 섹션 자동 갱신 (BIZ-74)
      window_days: 7                         # 활성 윈도우(일) — last_seen이 이 기간 안인 항목만 USER.md에 노출
      sidecar_path: ".agent/active_projects.jsonl"  # 모든 프로젝트 메타가 누적 보관되는 sidecar
```

`dreaming.model`을 지정하면 드리밍 요약에 특정 LLM 백엔드를 사용합니다. 예를 들어 일반 대화는 Claude를 쓰면서 드리밍은 비용이 낮은 Gemini로 처리할 수 있습니다.

### 무결성 원칙

- 드리밍은 `MEMORY.md` / `USER.md` / `SOUL.md` / `AGENT.md`의 **managed 마커 안쪽**만 수정합니다 — 마커 외부(정체성, 캘린더 매핑 등)는 read-only.
- `USER.md`에는 대화에서 명확히 드러난 정보만 추가되며, 민감한 개인정보(비밀번호, 금융정보)는 저장하지 않습니다.
- 클러스터 요약은 `<!-- cluster:N start/end -->` 마커 영역에만 기록되어 사용자가 수동 편집한 다른 영역과 충돌하지 않습니다.

### Protected Section 모델 (BIZ-72)

드리밍이 사용자의 정체성·캘린더 매핑·디렉토리 규약 같은 보호 영역을 침식하지 않도록, dreaming 쓰기는 다음 마커 안쪽으로만 허용됩니다:

```
<!-- managed:dreaming:<section> -->
... dreaming 갱신 영역 ...
<!-- /managed:dreaming:<section> -->
```

파일별 기본 섹션 이름:

| 파일 | 섹션 이름 | 용도 |
| --- | --- | --- |
| `MEMORY.md` | `journal` | 시간순 dreaming 기록 (레거시 모드) |
| `MEMORY.md` | `clusters` | 시맨틱 클러스터 요약 컨테이너 (Phase 3) — 안쪽에 `<!-- cluster:N start/end -->` 들이 들어간다 |
| `USER.md` | `insights` | dreaming-derived 사용자 인사이트 |
| `USER.md` | `active-projects` | 최근 N일 윈도우의 활성 프로젝트 카드 (BIZ-74) — sidecar `.agent/active_projects.jsonl`에서 렌더링 |
| `SOUL.md` | `dreaming-updates` | dreaming-suggested 성격/말투 변경 |
| `AGENT.md` | `dreaming-updates` | dreaming-suggested 행동 규칙 변경 |

#### Fail-closed 시맨틱

대상 파일 중 **하나라도** managed 섹션이 누락되거나 마커 짝이 맞지 않으면, 드리밍은 사이클 전체를 즉시 abort하고 어떤 파일도 변경하지 않습니다. 부분 변경(한 파일만 갱신되고 다른 파일은 abort) 같은 어정쩡한 상태는 만들어지지 않습니다.

- **사전 검증(preflight)**: `run()` 시작 시 `MEMORY/USER/SOUL/AGENT` 4종을 한꺼번에 검증하고, 하나라도 실패하면 백업조차 만들지 않고 종료합니다.
- **쓰기 도중 실패**: 드물지만 동시 편집 등으로 쓰기 도중 마커가 깨지면, 백업에서 4종 파일을 모두 복원합니다.
- **마커가 없는 파일을 자동 생성하지 않습니다** — 이건 의도된 동작입니다. dreaming은 사용자의 명시적 동의(템플릿에 마커를 넣는 행위) 없이 새로 시작하지 않습니다.

#### 운영 가이드

- 새 워크스페이스를 만들 때는 `.agent/USER.md` / `.agent/MEMORY.md` / `.agent/SOUL.md` / `.agent/AGENT.md` 템플릿에 위 표의 마커를 미리 넣어두세요. 본 저장소의 `.agent/*.md` 파일들이 starter 템플릿입니다.
- 마커를 실수로 지웠거나 형식을 깨뜨렸다면, 다음 dreaming 사이클은 silently abort되고 `Dreaming aborted (preflight): ...` 로그가 남습니다 — 로그를 보고 마커를 복구하면 됩니다.
- 드리밍이 보호 영역을 침범하지 않는지 회귀 검증하는 테스트는 `tests/unit/test_dreaming_protected_section.py`에 있습니다.

## Cron 메시지 격리

Cron job에서 발생하는 메시지는 `process_cron_message()`를 통해 처리되며, 일반 대화와 완전히 격리됩니다:

- 대화 히스토리를 로드하지 않음 (isolated 모드)
- 대화 DB에 저장하지 않음
- Cron 결과가 일반 대화 컨텍스트에 영향을 주지 않음

이를 통해 Cron 결과가 이전 대화에 의해 오염되거나, 반대로 Cron 출력이 일반 대화에 혼입되는 것을 방지합니다.

## 관련 파일

- `src/simpleclaw/memory/conversation_store.py` — SQLite 대화 저장소, 임베딩/클러스터 CRUD
- `src/simpleclaw/memory/models.py` — `ConversationMessage`, `MessageRole`, `ClusterRecord`
- `src/simpleclaw/memory/embedding_service.py` — sentence-transformers 임베딩 서비스
- `src/simpleclaw/memory/clustering.py` — `IncrementalClusterer` (코사인 부착, centroid 누적 평균)
- `src/simpleclaw/memory/dreaming.py` — 드리밍 파이프라인 (에피소드 + 클러스터 분기, Protected Section 가드)
- `src/simpleclaw/memory/protected_section.py` — managed 마커 파서 + 안전 쓰기 primitive (BIZ-72)
- `src/simpleclaw/daemon/dreaming_trigger.py` — 드리밍 트리거 조건 판단
