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
