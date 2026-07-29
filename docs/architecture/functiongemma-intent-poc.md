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

## Teacher/student 계약

weak label은 sanitized case를 production-native Unified Planner에 보내 생성한다.
Telegram send, skill/tool/recipe 실행, mutation, conversation persistence는 호출하지
않는다. provider call은 명시적 `--allow-provider-calls`가 있어야 하며 300회와
60분 중 먼저 도달한 cap에서 멈춘다. schema/boundary/PlanGate 실패, 낮은
confidence, creation-vs-execution 경계는 adjudication queue로 분리한다.

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
