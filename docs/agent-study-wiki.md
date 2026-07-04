# Agent Study Wiki — 설계 문서

> 상태: 설계 + config skeleton (Issue 1/11, BIZ-386)
> 범위: 경계 정의 · 데이터 모델 · 정책. 실제 runner/wiki 생성/retrieval 연동은 후속 이슈.

## 1. 목적 (Why)

Agent Study Wiki는 SimpleClaw가 **사용자 프로필/기억과 분리된 "외부 세계 배경지식"**을
매일 공부하고, 질문 시 맥락으로 활용하기 위한 저장소다.

사용자 관심사·Dreaming 결과·중요 뉴스를 바탕으로 주제(topic)를 만들고, 출처가 달린
사실 항목(entry)을 신선도/신뢰도와 함께 누적한다. 응답 시에는 신선하고 신뢰할 수 있는
항목만 골라 맥락으로 주입한다.

## 2. 사용자 메모리 vs Study Wiki — 경계 (핵심)

이 경계가 이 기능의 존재 이유다. 둘을 섞으면 두 가지 실패가 생긴다.

1. **과대 일반화**: 자동 뉴스 브리핑에서 한 번 본 주제가 "사용자 관심사"로 굳어진다.
2. **낡은 사실의 영속**: 시효가 지난 외부 사실이 사용자 메모리처럼 영구히 남는다.

따라서 두 저장소는 진실성 모델·시효·물리적 위치를 모두 달리한다.

| 구분 | 사용자 메모리 (USER.md / MEMORY.md / insights) | Study Wiki |
|---|---|---|
| 다루는 대상 | 사용자 자신 — 정체성, 선호, 관계, 결정 | 외부 세계 — 뉴스, 기술, 도메인 배경지식 |
| 진실성 모델 | 사용자가 말한 것이 곧 사실 (1인칭 권위) | 출처로 뒷받침되는 주장 (검증 대상) |
| 시효 | 사실상 영속 (명시적 변경 전까지 유효) | 신선도(freshness)로 감쇠 — 오래되면 신뢰 하락/만료 |
| 출처 | 대화 자체 | 외부 URL/문서 (필수, `safety.require_sources`) |
| 신뢰도 표시 | 불필요 | confidence 등급 + 저신뢰 시 면책 문구 |
| 생성 주체 | 사용자 발화 / Dreaming 승격 | study runner의 자동 수집 |
| 물리적 위치 | `~/.simpleclaw-agent/default/` (insights.jsonl 등) | `study.wiki_dir` (기본 `.../agent_wiki`) — **별도 디렉터리** |
| 과대 일반화 위험 | — | "본 적 있음" ≠ "관심사". `topic_evolution`으로 관심도 임계 통과 시에만 승격 |

**물리적 디렉터리 분리**가 1차 방어선이다. Study Wiki는 절대 사용자 메모리 파일에
기록하지 않으며, retrieval 시에도 출처/신선도 메타데이터를 보존해 "외부 배경지식"임을
드러낸다.

## 3. 비목표 (Non-goals)

- 실제 study runner 실행 로직 (후속 이슈).
- 실제 wiki 파일 생성/스키마 영속화 (후속 이슈).
- orchestrator/응답 파이프라인 retrieval 연동 (후속 이슈).
- 사용자 메모리 시스템 변경 — 이 기능은 메모리를 읽기만 하고 쓰지 않는다.

## 4. 데이터 모델

개념 모델만 정의한다(영속 포맷은 후속 이슈에서 확정).

### Topic
공부의 단위. 사용자 관심사/Dreaming/뉴스에서 도출된다.
- `id`, `title`, `interest_score` (0–1)
- 상태: `candidate` → (관심도 ≥ `min_interest_score`) → `active` →
  (≥ `promote_threshold`) → `promoted`(상시 추적) / (무관심 `decay_after_days` 경과) → `decayed`

### Entry
한 주제 아래의 개별 사실 항목.
- `summary`, `sources` (URL 목록, 비어 있으면 안 됨)
- `confidence`: `high` | `medium` | `low`
- `collected_at` (신선도 계산 기준 타임스탬프)

## 5. Freshness / Confidence 정책

retrieval 시 entry는 confidence 등급별 신선도 허용치(`retrieval.freshness_hours`)를
넘기면 만료로 간주해 맥락에서 제외하거나 강등한다.

| confidence | 기본 허용 신선도 | 의미 |
|---|---|---|
| high | 24h | 검증된 사실 — 하루 안의 것만 신뢰 |
| medium | 72h | 보통 — 3일 |
| low | 168h | 약한 신호 — 1주, 단 면책 문구 동반 |

- `safety.require_sources: true` — 출처 없는 entry는 저장/주입하지 않는다.
- `safety.low_confidence_requires_disclaimer: true` — low confidence 항목을 응답에
  쓸 때는 불확실성 면책 문구를 함께 제시한다.

## 6. Topic 진화 정책

`topic_evolution` 설정으로 주제의 생애주기를 제어한다.

- `auto_create`: 관심 신호에서 후보 주제를 자동 생성할지 여부.
- `min_interest_score` (0.55): 후보가 `active`가 되는 최소 관심도 — **과대 일반화 방어**.
- `promote_threshold` (0.70): 반복 관심 시 상시 추적 주제로 승격.
- `decay_after_days` (14): 일정 기간 관심 신호가 없으면 주제를 감쇠/제거 —
  **낡은 사실 영속 방어**.

## 7. 설정 (config.yaml `study:`)

기본값은 `src/simpleclaw/config_sections/study.py`의 `_STUDY_DEFAULTS`에 고정되어
있으며 `tests/unit/test_study_config.py`가 계약을 보장한다. 예시는
`config.yaml.example`의 `study:` 섹션 참고. 모든 기능은 opt-in(`enabled: false`)이다.

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
    freshness_hours: { high: 24, medium: 72, low: 168 }
  topic_evolution:
    auto_create: true
    min_interest_score: 0.55
    promote_threshold: 0.70
    decay_after_days: 14
  safety:
    require_sources: true
    low_confidence_requires_disclaimer: true
```

로더는 중첩 섹션을 재귀 병합하므로(`retrieval.freshness_hours.high`처럼) 한 키만
override 해도 나머지 기본값이 유지된다. `wiki_dir`은 `~` 확장 후 `Path`로 정규화된다.
