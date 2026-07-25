# Unified TurnPlanner architecture and evaluation

## 목적

Unified TurnPlanner는 ordinary user turn마다 한 번 실행되어 다음 상위 결정을 하나의
구조화 계약으로 만든다.

- 현재 질문과 후보 history의 관계
- downstream에 전달할 message ID
- history 없이도 실행 가능한 standalone question
- clarification 필요 여부
- current-fact 계획
- execution mode와 허용 asset/tool

이 문서는 production wiring이 아니라 fixed-gold evaluator의 책임과 acceptance
기준을 정의한다. production orchestrator 변경, live shadow 활성화, live 대화
수집은 BIZ-488 범위 밖이다.

## Outer turn과 inner loop

Planner는 outer turn의 상위 실행 결정을 소유한다. 정상 경로에서는 user turn당
한 번만 호출하며, 선택된 context는 해당 turn 동안 고정된다.

```text
user turn
  -> bounded context candidates
  -> Unified TurnPlanner                 # outer turn에서 1회
  -> local PlanGate
  -> ExecutionRouter
  -> selected controller
       -> ToolLoopRunner iteration       # inner loop
       -> ComplexFactWorkflow iteration  # inner loop
       -> Recipe lifecycle               # inner loop
  -> evidence/tool gates
  -> final composer
```

ToolLoopRunner, ComplexFactWorkflow, recipe controller의 반복은 inner loop다. 이
반복이 전체 Planner를 다시 호출하면 context selection과 execution mode가
중간에 흔들리고 token/latency가 다시 증가하므로 허용하지 않는다.

## Planner 평가와 evidence 평가의 분리

Planner evaluator는 “어떤 계획을 세웠는가”만 평가한다.

- context relation 정확도
- selected-turn precision/recall
- topic shift에서 과거 문맥을 선택하지 않았는지
- clarification과 execution mode
- 허용 가능한 asset
- current-fact 필요 여부와 domain/entity coverage
- 검색 query에 fixture가 금지한 미확인 claim을 주입하지 않았는지
- schema success, latency, token, context reduction

다음은 evidence evaluator 또는 controller/gate 테스트의 책임이다.

- 실제 source가 존재하고 신뢰 가능한지
- source가 claim을 지지하는지
- 인용문과 수치가 정확한지
- 최신성/as-of가 충분한지
- tool 결과에서 final answer까지 claim lineage가 유지되는지

과거 assistant 메시지는 conversational context일 뿐 evidence가 아니다. Planner
점수에 source/claim correctness를 섞지 않아야 두 계층의 실패 원인을 구분할 수
있다.

## Fixed-gold fixture

SoT는 `tests/fixtures/unified_turn_planner_cases.jsonl`이다. 각 줄은 독립 JSON
object이며 최소한 다음 필드를 가진다.

```json
{
  "id": "sk-nvidia-followup",
  "critical": true,
  "history": [
    {"id": "m101", "role": "user", "content": "…"},
    {"id": "m102", "role": "assistant", "content": "…"}
  ],
  "current": "…",
  "gold": {
    "context_relation": "same_thread",
    "selected_turn_ids": ["m101"],
    "clarification_required": false,
    "execution_mode": "fact_check",
    "acceptable_assets": ["realtime-lookup-skill"],
    "fact_required": true,
    "domains": ["news"],
    "entities": ["SK", "NVIDIA"],
    "entity_aliases": {"NVIDIA": ["엔비디아"]},
    "normalized_terms": [["NVIDIA", "엔비디아"]],
    "forbidden_query_terms": ["2GW"]
  }
}
```

`prediction`과 `metrics`는 evaluator 자체를 네트워크 없이 회귀 검증하기 위한
고정 replay다. production planner가 도입되면 live/shadow 결과를
`score_prediction()`에 전달한다. gold와 replay는 report에 직렬화하지 않는다.

### Prediction fail-closed 경계

Evaluator는 score 계산 전에 자신이 소비하는 전체 prediction shape를 검증한다.
다음 필드가 누락되거나 타입이 다르면 semantic score를 계산하지 않고
`schema_valid=false`, `passed=false`로 종료한다.

- `context`: enum `relation`, history 후보에 속하는 string array
  `selected_turn_ids`, non-empty string `standalone_question`
- `clarification.required`: boolean
- `domains`: string array
- `fact_check`: boolean `required`, non-empty string `domain`, string array
  `entities`, string `search_query`
- `execution`: enum `mode`, `primary_asset`

`fact_check.required=false`여도 `entities`와 `search_query`의 container/type은
동일하게 강제한다. `execution.primary_asset`은 `null`, `"__none__"`, 또는
정확히 `{"asset_type": "skill"|"recipe", "name": "<non-empty>"}`인 object만
허용한다. 알 수 없는 selected ID, asset object의 오타·추가 key, 다른 sentinel은
모두 schema 오류다.

오류에는 `invalid:<field>` 또는 `missing:<object>` 형식의 고정 code만 남긴다.
잘못된 payload 값, 검색어, credential은 report나 오류에 포함하지 않는다.

## Report와 privacy

모든 baseline은 `turn-planner-eval.v1` report schema를 공유한다.

- `benchmark`: `baseline`, `reasoning`, `repeat`, `live`
- `summary`: schema/pass/macro/critical rate, selected precision/recall
- `summary.latency_ms`: average, p50, p95
- `summary.tokens`: input/output total과 average
- `summary.context_reduction_rate`
- `relations`: context relation별 pass/selection 지표
- `cases`와 `failures`: case ID, check 이름, error code만 포함

Report에는 다음 값을 기록하지 않는다.

- history/current/standalone question 원문
- selected message content
- 검색 query와 tool argument
- API key, credential, user/chat ID
- raw planner response

실패 메시지도 payload 값을 포함하지 않는 고정 error code로 제한한다.

### Shadow telemetry와 acceptance report

`agent.unified_turn_planner.mode=shadow`는 ordinary turn의 기존 응답 경로와
독립된 sampled background task만 실행한다. default는 `off`,
`sample_rate=0.0`이며 live shadow 활성화와 sample rate 변경은 별도 운영자
승인을 받아야 한다. `primary`는 후속 production 전환을 위한 예약 값으로,
현재 구현에서는 응답 route/tool/context를 바꾸지 않는다.

structured log의 `action_type=unified_turn_plan_shadow` 항목은 다음처럼 evaluator
지표에 대응한다.

| Shadow field | Acceptance/evaluator mapping |
|---|---|
| `ok` | planner schema/service success count |
| `relation` | relation별 sample 분포 |
| `selected_turn_count` | 선택 ID 수; content는 기록하지 않음 |
| `execution_mode`, `asset_count`, `fact_required` | mode/asset/fact 분포 |
| `gate_status`, `violation_codes` | pass/clarify/confirmation/repair/reject와 unknown asset 등 stable gate code |
| `latency_ms` | planner p50/p95 |
| `input_tokens`, `output_tokens` | primary와 validated retry를 합친 token total/average |
| `candidate_context_chars`, `selected_context_chars` | downstream 예상 history chars 감소율 |
| `catalog_fingerprint` | 같은 runtime catalog snapshot 여부 |

원문, selected turn content, standalone question, 검색 query, entity, tool argument,
credential, user/chat ID는 event API에 필드가 없으며 기록하지 않는다. 실패도 예외
본문 대신 `planner_unavailable` 같은 stable `error_code`만 기록한다.
또한 shadow structured row는 `trace_id=""`를 명시해 ordinary turn의
contextvar trace를 상속하지 않는다. 따라서 redacted aggregate를 같은 turn의 다른
action log와 trace로 재결합할 수 없다.

`structured_output`과 `repair_attempts`는 rollout 중 조절 가능한 knob가 아니라
strict planner compatibility 계약이다. loader는 사용자 입력과 관계없이 각각
`true`와 `1`로 fail-closed 정규화한다. provider schema와 deterministic
repair/validated semantic retry를 완화하려면 별도 설계·검증이 필요하다.

Acceptance report 생성 절차:

1. 승인된 기간의 `execution_YYYYMMDD.log`에서
   `action_type == "unified_turn_plan_shadow"` 항목만 선택한다.
2. 각 log row의 `details`에 `event=action_type`,
   `latency_ms=round(duration_ms)`를 합쳐
   `TurnPlannerShadowEvent`로 복원한다.
3. `aggregate_turn_planner_shadow_events(events)`를 호출해 sample 수,
   gate/relation/mode 분포, p50/p95, token 합계/평균, context reduction을 만든다.
4. 같은 catalog fingerprint의 fixed-gold report와 현재 two-stage live baseline을
   대조한다. critical omission과 topic-shift 오사용은 gold 또는 승인된 label이
   있는 평가 집합으로 판정하며, unlabeled shadow telemetry만으로 추정하지 않는다.
5. sample 100개 이상과 아래 Shadow-to-primary gate를 모두 확인하고, event/report
   JSON에 승인된 canary 원문·credential marker와 ordinary turn correlation
   identifier가 각각 0건인지 별도 leak scan한다.

## 실행

오프라인 replay는 외부 API나 runtime state를 사용하지 않는다.

```bash
.venv/bin/python scripts/eval_unified_turn_planner.py \
  --fixture tests/fixtures/unified_turn_planner_cases.jsonl \
  --repeat 3 \
  --reasoning medium \
  --json-output /tmp/unified-turn-planner-report.json
```

지원 reasoning label은 `off`, `low`, `medium`이다. 현재 two-stage baseline도
동일한 `aggregate_results(..., baseline="two_stage")` 계약으로 저장한다.

`--live`는 명시적 opt-in flag다. BIZ-488에서는 production planner와 operator
quota 승인이 아직 없으므로 live runner를 연결하지 않고 fail-closed한다. 후속
planner 이슈가 runner를 연결하더라도 `--live` 없는 외부 호출은 금지한다.

## Acceptance criteria

Fixed-gold gate:

- JSONL fixture 30개 이상
- SK–NVIDIA initial/follow-up critical fixture 포함
- `standalone`, `same_thread`, `related_reference`, `topic_shift`, `unclear`
  관계별 3개 이상
- history 8개 이상인 case 최소 1개와 비인접 관련 user/correction 선택 포함
- long-history gold는 과거 assistant claim과 최신 무관 turn을 제외하며, 무관 ID
  주입 시 context-selection gate 실패
- long-history context reduction은 선택 history 문자 수 / 전체 후보 history 문자 수로
  결정론적으로 계산
- schema success 100%
- critical case pass 100%
- topic shift의 과거 문맥 선택 0건
- current-fact critical omission 0건
- raw text/credential report 유출 0건

Shadow-to-primary gate:

- 100개 이상 shadow sample
- unknown asset의 PlanGate 통과 0건
- planner p50이 현재 two-stage p50의 75% 이하
- planner p95가 현재 two-stage p95의 80% 이하
- downstream 예상 history 문자 수 40% 이상 감소
- mutation direct auto-execution 0건

Fixed replay의 latency는 report schema와 percentile 계산을 검증하는 표본이며 live
성능 acceptance 증거가 아니다. 실제 전환 판단은 승인된 live benchmark와 shadow
telemetry를 같은 report schema로 집계한 결과를 사용한다.
