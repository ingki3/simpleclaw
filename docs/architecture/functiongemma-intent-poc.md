# FunctionGemma intent·asset classifier PoC

## 목적과 비목표

BIZ-512는 `google/functiongemma-270m-it`가 SimpleClaw의 bounded context와 최대
12개 candidate asset에서 축소된 intent·asset 결정을 모사할 수 있는지 확인하는
비운영 PoC다. 전체 `UnifiedTurnPlan`, 안전 확인, side effect, tool allowlist,
current-fact gate를 학생 모델에 위임하지 않는다. 이 결정은 계속 production
catalog와 `PlanGate`가 소유한다.

학생 모델은 단일 native function `classify_intent_and_select_asset`만 반환한다.
arguments는 `context_relation`, `execution_mode`, `domains`, `intents`,
`primary_asset`, `fallback_required`로 고정한다. `primary_asset`은 현재 case의
candidate ID 또는 `__none__`이어야 하며 알 수 없는 값은 hard failure다.

## 데이터 계보와 privacy boundary

`functiongemma_dataset.py`는 SQLite URI `mode=ro`와 `PRAGMA query_only`로
non-deleted 메시지를 읽는다. user turn을 최대 300개 case로 만들기 전에 이메일,
전화, URL path, user/chat/message ID, private absolute path, credential-like
text를 placeholder로 치환한다. source group을 먼저 train/dev/test로 분리한 뒤
train에만 augmentation을 적용한다.

fixed-gold 31건과 private historical 24건의 current-text fingerprint는 source
추출 단계에서 제외하고 augmentation seed로도 거부한다. raw DB row, assistant
response, private path, credential은 Git, Multica, public report에 기록하지 않는다.
상세 artifact는 기본 `0700` 디렉터리와 `0600` 파일로만 저장한다. 공개 가능한
산출물은 aggregate count, metric, 비가역 SHA-256 fingerprint뿐이다.

DB row ID는 source case와 context turn ID에 사용하지 않는다. 동일 seed와
추출 순서로 만든 run-local opaque ID만 provider-facing case에 남기며, source
lineage에는 sanitized text의 비가역 fingerprint만 보존한다. 실제 Unified
Planner request 조립 결과는 provider 호출 전에 raw `msg:*`/`live:*` 및
user/chat/message ID 패턴이 없는지 fail-closed 진단한다.

## Teacher/student 계약

weak label은 sanitized case를 production-native Unified Planner에 보내 생성한다.
Telegram send, skill/tool/recipe 실행, mutation, conversation persistence는 호출하지
않는다. provider call은 명시적 `--allow-provider-calls`가 있어야 하며 300회와
60분 중 먼저 도달한 cap에서 멈춘다. schema/boundary/PlanGate 실패, 낮은
confidence, creation-vs-execution 경계는 adjudication queue로 분리한다.
case별 candidate set과 fingerprint는 provider 호출 전에 한 번만 고정한다.
Unified Planner에는 그 set에 대응하는 runtime-visible catalog만 노출하며, 호출
뒤 target을 candidate에 추가하거나 재정렬하지 않는다. set 밖 target은
`boundary.unknown_asset` adjudication으로 분리한다.

teacher와의 점수는 절대 정확도가 아니라
`Gemini/Unified emulation quality`로만 해석한다.

## 학습과 재현

MLX-LM은 `/opt/homebrew/bin/mlx_lm`을 사용한다. 모델 revision은
`39eccb091651513a5dfb56892d3714c1b5b8276c`, quantization은 4-bit/group-size
64, LoRA는 all layers, prompt masking과 seed 42를 고정한다. resume, W&B,
MLflow, SwanLab, shared cache 업로드는 금지한다. 한 번의 run만 수행하며
3 epochs, 5,000 steps, 2시간, artifact 10GB 중 먼저 도달한 제한에서 종료한다.

```bash
.venv/bin/python scripts/dev/run_functiongemma_intent_poc.py all \
  --live-db ~/.simpleclaw-agent/default/conversations.db \
  --private-output-dir \
  ~/.simpleclaw-agent/default/evaluations/functiongemma-intent-YYYYMMDD-HHMMSS \
  --config config.yaml \
  --allow-provider-calls \
  --max-provider-calls 300 \
  --allow-model-download
```

기본 실행은 live DB와 provider/model download를 건드리지 않고 fail-closed한다.

## 평가와 판정

base와 tuned adapter는 동일 held-out case/order/seed/candidate catalog로 실행한다.
API 성공, native function-call, schema, candidate boundary, context relation,
execution mode, domains/intents macro precision·recall·F1, primary asset,
fallback recall, p50/p95를 분리한다. unknown/out-of-candidate asset,
schema/boundary 위반, missing fallback은 soft 평균과 별도 hard failure다.

추천 조건은 tuned hard failure 0과 compact macro score base 대비 10%p 이상
개선이다. 미달이면 실패 결과와 원인을 기록하고 live integration을 제안하지
않는다. 이 PoC는 live config 변경, restart, deploy 또는 request-path 연결을
수행하지 않는다.

## BIZ-512 실제 결과

> **Invalidated (BIZ-513):** 아래 44개 weak label 중 41개는 planner에 노출된
> 최초 candidate set 밖 target을 호출 후 candidate 재작성으로 수용했다. 또한
> raw DB row ID가 provider context turn ID에 포함됐다. 따라서 해당 label 44개,
> 그 파생 augmentation, 두 adapter, aggregate report와 아래 수치는 bounded
> candidate/privacy PoC 근거로 사용할 수 없다. artifact는 삭제하지 않고
> lineage상 invalidated 상태로만 보존한다.

- live DB: non-deleted row 938건은 추출 전후 동일했다. user turn 469건을
  scan하고 source case 300건을 만들었다.
- sealed exclusion: fixed-gold 31건과 historical 24건에서 중복을 제거한
  53개 current-text fingerprint를 train/augmentation seed에서 제외했다.
- teacher: 첫 catalog 진단 시도 14회와 최종 labeling 286회를 합쳐 provider
  300회 hard cap을 지켰다. strict label 44건, adjudication 256건이었다.
- leakage: dev/test와 동일 fingerprint인 train 6건을 제외했다. 최종 원본
  train/dev/test는 31/3/4, augmented train은 124건이며 group leakage는 0이다.
- training: exact revision을 4-bit/group64로 변환하고 native
  `messages + tools + assistant.tool_calls` 형식으로 all-layer LoRA 100 step,
  seed 42를 실행했다. 47.65초, adapter 15,242,119 bytes였다.
- format correction: 초기 plain-JSON completion trial은 FunctionGemma 공식
  control-token 계약과 맞지 않아 판정에서 제외했다. 최종 수치는 위 native-format
  단일 run만 사용한다.
- held-out: base/tuned 모두 inference API success 100%였다. base native-call
  25%이나 strict schema 0%, tuned native-call/schema/boundary 0%였다.
  compact macro score는 base/tuned 모두 0.0으로 개선 0%p다.
- hard failures: base는 schema/native 4건, tuned는 schema 4건이다. unknown
  asset, missing fallback, privacy failure는 각각 0건이지만 schema hard gate를
  통과하지 못했다.
- 결론: `recommend_shadow_integration=false`. 이 데이터 크기와 3-epoch 이내
  budget에서 feasibility 추천 조건을 충족하지 못했다.

Aggregate report fingerprint:
`8cb7e9effcc6908e1907a1736786470edc9ea8afa7b47d93b6b47d24ed973a48`.

## BIZ-515 clean rerun 결과

기존 `functiongemma-intent-20260729-biz512` 디렉터리는 삭제하지 않았다.
`INVALIDATED.json`에 기존 44 label, augmentation, plain-JSON/native-format
adapter 2개와 aggregate report의 file SHA-256 및 invalidation 사유를 기록했고,
fresh run은 marker SHA만 lineage에 포함해 기존 artifact를 metric 입력에서
제외했다.

비용 실행 당시 private directory에는
`functiongemma-intent-poc/biz-515-v1` fingerprint
`a603002a0209644b43a5cc3950e3dca498b2bf250605e68edfb9678baf738ab9`가
기록됐다. 그러나 이 v1 계약은 reviewed prerequisite와 source를 문자열로만
표현해 exact 실행 source를 증명하지 못한다.

후속 `biz-515-v2` 계약은 prerequisite PR #527 merge SHA
`b1c659b5821fe45368596e92a8d67464503e7fd6`와 runner를 포함한
task-owned FunctionGemma source 8개 파일의 exact file-byte SHA-256을 포함한다.
현재 checkout에서 prerequisite ancestry와 모든 file hash를 대조해 하나라도
다르면 provider/training 전에 fail-closed한다. reviewed v2 fingerprint는
`3ad807841aff40042e07d20d66833b927847f2307a5ddbfaacc022eb98438863`다.

- extraction: DB non-deleted row는 실행 전후 938건으로 동일했다. source 300건의
  split은 train/dev/test 236/34/30이며 source-group leakage는 0이다.
- provider: 300-call hard cap에서 종료했다. 968.079초, 20,485 tokens를
  소비했고 accepted 4건(train/dev/test 3/0/1), adjudication 296건이었다.
  `boundary.unknown_asset` 21건을 포함한 out-of-set 결과는 accepted되지 않았다.
- payload audit: 실제 provider-neutral `LLMRequest` 300건을 원문 없이 canonical
  JSON SHA-256으로 감사했다. raw DB/user/chat/message identifier match와 accepted
  target out-of-pre-call-set은 모두 0이다. payload-set fingerprint는
  `f42f398a7de0dea7cecd1c3d08815c9dcb1e5fc914cf297784255b69e2d4b710`이다.
- augmentation: accepted train 3건에서 필수 strata를 포함한 12건을 생성했다.
  최종 MLX train/valid/test row는 15/0/1이다.
- training: fresh run의 MLX QLoRA process invocation은 정확히 1회다. seed 42,
  요청 45 steps로 시작했으나 3.019초 뒤 return code 1, `process_error`로
  종료했고 adapter artifact는 0 bytes다. 당시 runner가 child stderr를
  폐기했기 때문에 내부 예외를 사후 단정하지 않는다. 이후 runner는 private
  stdout/stderr log와 `process_error` stop reason을 보존하도록 보완했다.
- evaluation: tuned adapter가 생성되지 않아 base/tuned 비교를 실행하지 않았다.
  aggregate hard failure는 `training.process_error=1`,
  `lineage.execution_source_unverifiable=1`,
  `recommend_shadow_integration=false`다. 추가 seed/run/budget 확대는 수행하지
  않았다.
- source provenance: Multica run message와 Git history로 prerequisite checkout
  base SHA는 복원했지만, 비용 실행은 uncommitted worktree에서 수행됐고
  runner/report 보완이 실행 후 최초 commit `8106371bee55ca64fcbe6a2dde7e5a05940e06b1`
  에 함께 저장됐다. 따라서 exact execution commit/tree/task-owned file hash는
  복원할 수 없다. 이 한계를 private run/lineage/aggregate manifest에
  `status=unverifiable` hard failure로 기록했으며 기존 process를 재실행하지
  않았다.

알려진 로컬 MLX 학습 실행 이력은 호환성 2-step smoke 1회, invalidated
plain-JSON/native-format adapter run 각 1회, BIZ-515 fresh process 1회로 총
4회다. BIZ-515 fresh directory 내부 invocation count는 1이며 기존 run은 final
metric에 포함하지 않는다.

hard-failure report는 payload canonical SHA-256과 private report file-byte
SHA-256을 알고리즘·canonicalization과 함께 별도 필드로 기록한다. 값은 각각
`cea58e6e38006e90ecc1a534dc5325baab0b9ada2f67fe3bffc6a8209b2d6b29`,
`86902022b9bf5623996eb9f577368ec973008596e0ad60e3d271b5f784cd7c2b`이다.
live config, restart, deploy, request-path 연결 및 private artifact 외부 업로드는
수행하지 않았다.
