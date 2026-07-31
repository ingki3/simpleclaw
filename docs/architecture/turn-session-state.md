# Turn/Session State Contract

## 결론

일반 사용자 턴의 의미 판단은 `UnifiedTurnPlan` 한 번으로 끝냅니다. 같은 턴의
`TurnExecutionState`가 plan, gate, 실행 범위, evidence, action ledger와 final을
소유하며, `SessionState`는 턴을 넘어 필요한 pending interaction과 마지막 완료
turn ID만 영속화합니다.

## 소유권

| 상태 | 수명 | 소유 항목 | 금지 항목 |
|---|---|---|---|
| `SessionState` | 여러 턴·프로세스 재시작 | hashed session key, versioned pending interaction, last completed turn ID | intent, domain, route, evidence, raw prompt/tool output |
| `TurnExecutionState` | 단일 `process_message` | immutable plan, gate, tool scope, evidence, action ledger, limitations, final | credential, raw prompt, 다른 session의 context |
| `UnifiedTurnPlan` | 단일 턴 계획 이후 불변 | context IDs, domain, intents, typed entities, reference date, execution mode/allowlist | provider 결과, 실행 중 재분류 |

Session key는 `(channel, user_id, chat_id, thread_id)`의 canonical JSON 배열을
SHA-256으로 해시합니다. DB message/context 조회와 Telegram pending callback은
항상 같은 key를 사용합니다.

## 상태 전이

```text
received -> planned -> plan_gated
                         |-> waiting_for_user
                         |-> rejected
                         `-> executing
                              |-> finalizing -> completed
                              `-> collecting_evidence
                                   |-> evidence_verified -> finalizing -> completed
                                   `-> limited_final -> completed
```

선언되지 않은 edge는 `InvalidTurnTransition`입니다. `fact_check.required=true`인
턴은 `EvidenceStatus.VERIFIED` 전에는 `FINALIZING`으로 갈 수 없습니다.

## Evidence 결과

- `FAILED`: provider/transport 실행이 실패했습니다. “결과 없음”을 뜻하지 않습니다.
- `UNUSABLE`: 응답은 왔지만 freshness/relevance/claim 계약을 만족하지 않습니다.
- `NOT_FOUND`: authoritative source가 명시적으로 빈 결과를 확인한 경우만 사용합니다.
- `VERIFIED`: current-turn action으로 수집한 usable evidence를 controller가 검증했습니다.

실패·미지원·unusable 결과는 deterministic limited final로 끝납니다. 과거 assistant
메시지나 RAG 문장은 current-turn evidence가 될 수 없습니다.

## 의미 판단 경계

`UnifiedTurnPlan.execution.mode`가 ordinary turn 상위 분기의 유일한 semantic
source of truth입니다. planner 실패는 fail-closed이며 `TurnAnalysis`,
`TurnFrame`, response/capability keyword classifier로 내려가지 않습니다.

허용되는 결정적 parsing은 slash command, callback ID, provider schema/status,
보안 guard 및 source 문서 구조처럼 의미 route를 고르지 않는 경계 처리뿐입니다.

Few-shot은
`prompts/system/unified_turn_planner_examples.yaml`에서 base prompt와 별도 version
관리합니다. schema를 바꿀 때 base prompt, examples version, parser/PlanGate
contract test를 같은 변경으로 갱신합니다. prompt 원문은 telemetry에 기록하지 않고
name/version만 기록합니다.

## LangGraph를 사용하지 않는 이유

현재 흐름은 한 planner와 bounded controller가 명시적 phase enum으로 충분히
검증됩니다. 새 graph dependency는 상태 소유권을 분산시키고 migration 면적만
늘립니다. durable multi-node checkpoint/resume, 여러 human approval node,
병렬 DAG join이 실제 요구사항이 될 때 재검토합니다.
