# Agent Study Wiki 설계 문서

SimpleClaw가 사용자의 관심사, Dreaming 결과, 중요 뉴스를 매일 공부해 **외부 세계 배경지식**으로 축적하고, 질문 시 맥락으로 활용하기 위한 저장소다. 본 문서는 데이터 모델과 정책의 단일 기준점이며, 실제 runner/retrieval 구현은 후속 이슈에서 이 경계를 따른다.

> 범위 안내: 이 문서가 다루는 Issue 1의 산출물은 **설계 경계 + config 스켈레톤**까지다. study runner 실행, 실제 wiki 파일 생성, retrieval 연동은 비목표(후속 이슈)다.

## 목적 (Why)

- 사용자가 자주 묻거나 관심을 보인 주제, Dreaming이 발견한 인사이트, 매일의 중요 뉴스를 **에이전트가 스스로 공부**해 배경지식으로 쌓는다.
- 질문이 들어오면 이 배경지식을 freshness/confidence와 함께 맥락으로 주입해 답변 품질을 높인다.

## 비목표 (Non-goals)

- **사용자 프로필/기억을 대체하지 않는다.** Study Wiki는 "세계"에 대한 지식이지 "사용자"에 대한 사실이 아니다.
- 실시간 검색 엔진/뉴스 리더를 대체하지 않는다. 매일 배치로 갱신되는 **스냅샷**이다.
- 출처 없는 주장을 저장하지 않는다(아래 safety 정책 참조).

## 사용자 메모리 vs Study Wiki 경계 (핵심)

이 경계가 본 기능의 설계 동기다. 두 저장소를 섞으면 다음 문제가 생긴다.

- 자동 뉴스 브리핑에서 **한 번 본 주제**가 "사용자 관심사"로 과대 일반화된다.
- 오래된 외부 사실(예: 작년 환율, 지난 분기 실적)이 **사용자 메모리처럼 영속**으로 남아 낡은 정보를 사실처럼 말하게 된다.

| 구분 | 사용자 메모리 (USER.md / MEMORY.md / long-term insights) | Agent Study Wiki |
|---|---|---|
| 대상 | **사용자** 자신에 대한 사실·선호·관계 | **외부 세계**에 대한 배경지식 |
| 출처 | 사용자 발화, 대화 누적, Dreaming 승격 | 외부 검색/뉴스 + Dreaming이 식별한 학습 topic |
| 진실성 모델 | 사용자가 정정하기 전까지 유효(영속) | freshness로 시효가 있으며, 시간이 지나면 신뢰 하락 |
| 신뢰도 표기 | 일반적으로 단정 | confidence/출처를 답변에 동반(필요 시 면책 문구) |
| 저장 위치 | `~/.simpleclaw-agent/default/` 메모리 파일 | `study.wiki_dir`(기본 `.../agent_wiki`) — **물리적으로 분리** |
| 승격 트리거 | 반복 언급·확인 | `topic_evolution`의 관심도 임계값 |

물리적 디렉터리 분리(`wiki_dir`)로 "관심사"와 "세계 배경지식"을 파일 시스템 레벨에서부터 섞이지 않게 강제한다.

## 데이터 모델

Study Wiki는 **topic** 단위로 구성된다. 각 topic은 하나 이상의 **fact/entry**를 가지며, 각 entry는 출처와 메타데이터를 동반한다(실제 직렬화 포맷은 후속 이슈에서 확정).

- **Topic**
  - `id` / `title`: 주제 식별자.
  - `interest_score` (0.0–1.0): 사용자 관심도 추정. `topic_evolution`에서 생성/승격/감쇠 판단에 사용.
  - `status`: `candidate` → `active` → `decayed`.
  - `last_studied_at`: 마지막 공부 시각.
- **Entry (fact)**
  - `summary`: 한 줄 사실/요약.
  - `sources`: URL/출처 목록(safety.require_sources=true면 비어 있을 수 없음).
  - `confidence`: `high` / `medium` / `low`.
  - `captured_at`: 수집 시각 — freshness 계산의 기준.

## Freshness / Confidence 정책

- **Freshness**는 `captured_at` 기준 경과 시간으로 판정한다. `retrieval.freshness_hours`의 등급별 한계(`high`/`medium`/`low`)를 넘으면 해당 entry는 신선도가 떨어진 것으로 간주한다.
  - 기본값: `high=24h`, `medium=72h`, `low=168h(7일)`. 등급이 낮을수록 더 오래된 정보까지 허용한다.
- **Confidence**는 entry 단위로 부여한다. `safety.low_confidence_requires_disclaimer=true`면 `low` confidence 정보를 맥락으로 쓸 때 답변에 면책 문구를 동반한다.
- **require_sources**: 출처가 없는 사실은 기록 자체를 거부한다(기본 true).

## Topic 진화 정책 (`topic_evolution`)

1. **auto_create**: 관심도가 `min_interest_score`(기본 0.55) 이상인 후보 topic을 자동 생성한다(true일 때).
2. **promote**: 관심도가 `promote_threshold`(기본 0.70) 이상이면 정식(active) topic으로 승격해 매일 공부 대상에 포함한다.
3. **decay**: `decay_after_days`(기본 14일) 동안 갱신/언급이 없으면 topic을 감쇠 처리해 우선순위에서 제외한다. 낡은 외부 지식이 영속하지 않도록 하는 안전장치다.

## Config 스키마

`study:` 섹션은 `config_sections/study.py`의 `load_study_config()`가 로드하며, 모든 기본값은 비활성(opt-in)이다. study runner는 외부 검색/LLM 비용을 유발하므로 사용자가 명시적으로 켜야 한다.

```yaml
study:
  enabled: false
  wiki_dir: ~/.simpleclaw-agent/default/agent_wiki
  daily:
    enabled: false
    hour_kst: 6
    max_topics_per_run: 8
    max_sources_per_topic: 5
  retrieval:
    enabled: false
    top_k: 4
    max_context_chars: 5000
    freshness_hours:
      high: 24
      medium: 72
      low: 168
  topic_evolution:
    auto_create: true
    min_interest_score: 0.55
    promote_threshold: 0.70
    decay_after_days: 14
  safety:
    require_sources: true
    low_confidence_requires_disclaimer: true
```

- `wiki_dir`는 로더에서 `~` 확장을 거쳐 `Path`로 정규화된다.
- 누락된 하위 키는 기본값으로 병합되며, `study` 섹션이 dict가 아니거나 파일이 없으면 전체 기본값을 반환한다.

## 후속 작업

- Issue 2 이후: study runner 실행, wiki 파일 직렬화, retrieval 연동, Dreaming/뉴스 파이프라인과의 결합.
