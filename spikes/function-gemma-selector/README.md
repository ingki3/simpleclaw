# Function Gemma/Gemini Selector Spike

## 목적

SimpleClaw 오케스트레이터가 매 턴 모든 스킬/레시피 목록을 system prompt와 tool schema에 넣는 구조를 줄일 수 있는지 확인하기 위해, Gemini 계열 function-calling 모델을 **사전 selector**로 사용하는 실험을 수행했습니다.

이번 스파이크는 운영 코드(`src/simpleclaw/...`)를 수정하지 않고, `spikes/function-gemma-selector/` 아래의 일회성 평가 스크립트만 추가합니다.

## 실험 질문

Given 현재 live 스킬/레시피 manifest가 있을 때,
When 작은 Gemini/function-calling 모델에 사용자 발화를 주고 `select_assets` 함수 호출을 요구하면,
Then 필요한 스킬/레시피를 높은 recall로 고르고 애매한 요청은 fallback 처리할 수 있는가?

## 데이터셋 / manifest

- config: `config.yaml`
- 스킬 로더: `simpleclaw.skills.discovery.discover_skills()`
- 레시피 로더: `simpleclaw.recipes.loader.discover_recipes()`
- 측정 시점 manifest: **32개 asset**
  - skills: 28
  - recipes: 4
- 레시피 4개는 현재 primary `~/.simpleclaw/recipes`가 아니라 legacy `.agent/recipes` fallback에서 로드되었습니다.

대표 발화는 browser, docs, Gmail, Calendar, PDF, PPTX, XLSX, 뉴스, 주식, 쇼핑, 경로, 레시피 및 모호한 요청을 포함한 16개입니다.

## 실행 방법

```bash
# manifest 통계만 확인
.venv/bin/python spikes/function-gemma-selector/evaluate_selector.py --manifest-only

# Gemini backend로 전체 평가 실행
.venv/bin/python spikes/function-gemma-selector/evaluate_selector.py \
  --output spikes/function-gemma-selector/selector_results.json \
  --markdown spikes/function-gemma-selector/selector_results.md
```

스크립트는 기본적으로 config의 provider 중 `gemini`가 있으면 `gemini` backend를 사용합니다. 다른 backend를 쓰려면 `--backend <name>`을 지정합니다.

## 결과

상세 결과는 [`selector_results.md`](./selector_results.md)와 [`selector_results.json`](./selector_results.json)에 기록했습니다. BIZ-310 재측정은 production guardrail(`simpleclaw.agent.asset_selector.normalize_selector_response`)을 적용한 후 산출했습니다.

| 지표 | 결과 |
|---|---:|
| manifest assets | 32 |
| samples | 16 |
| native tool-call success | 94% |
| parse success | 94% |
| top-k recall | 100% |
| top-k precision | 100% |
| fallback accuracy | 100% |
| avg latency | 3108.5 ms |
| p95 latency | 7761.7 ms |

### 잘 된 점

- 명확한 asset 매칭 14개 중 13개가 정확히 선택되었습니다.
- browser, Context7, Calendar, Google Docs, PPTX, PDF, XLSX, 뉴스, 미국 주식, 네이버 쇼핑, 로컬 경로, AI 리포트 레시피, 한국 증시 레시피는 모두 expected asset을 정확히 반환했습니다.
- 모호한 요청과 asset 없는 요청은 fallback으로 처리되어 오선택을 피했습니다.

### 실패 / 주의 케이스

- BIZ-310 guardrail 적용 후 샘플셋 기준 recall/precision/fallback accuracy는 100%로 재측정되었습니다.
- `no-asset` 케이스는 모델이 function-call 없이 빈 응답을 반환했지만 guardrail이 fallback으로 처리해 오선택을 피했습니다. function-call 누락은 production 경로에서 main LLM 재판단 신호로 유지합니다.
- 평균 3.1초, p95 약 7.8초는 매 턴 앞단 selector로 넣기에는 체감 지연이 큽니다. 메인 모델 prompt/context 절감액과 latency 비용을 함께 비교해야 합니다.
- 현재 스크립트는 live `config.yaml`을 읽으므로 API key/provider 설정에 의존합니다. API 실패 시 평가 결과의 errors에 실패 사유가 남습니다.

## Verdict: PARTIAL

### Recommendation for the real build

- **바로 제품 경로에 강제 도입하지 말고, shadow-mode 계측부터 권장합니다.**
  - 메인 오케스트레이터는 기존 full manifest 경로를 유지합니다.
  - selector 결과만 구조화 로그에 남겨 recall/latency를 더 큰 실제 트래픽에서 측정합니다.
- 도입 조건은 최소 `parse_success >= 99%`, `top-k recall >= 95%`, `p95 latency <= 1.5s` 수준으로 두는 것이 안전합니다.
- BIZ-310에서 구현한 fallback 안전장치를 유지해야 합니다.
  - tool_call 없음 / parse 실패 / confidence 낮음 / selected empty / ambiguous intent → 기존 full manifest 경로로 fallback
  - recipe는 “실행/돌려줘/브리핑/리포트/보내줘/정기” 등 명시적 실행 의도가 있을 때만 후보로 유지
  - 선택된 asset name이 manifest에 없으면 즉시 폐기
  - selector가 실패해도 사용자 요청 처리는 실패하지 않아야 함
- latency를 낮추려면 후보를 전체 32개가 아니라 lexical/BM25 1차 필터 top-N으로 줄인 뒤 function-calling selector를 호출하는 2단계 구조가 더 적합합니다.

## 산출물

- `evaluate_selector.py`: live manifest 로딩 + Gemini function-calling 평가 스크립트
- `manifest.json`: 측정 시점 manifest dump
- `selector_results.json`: 기계 판독용 결과
- `selector_results.md`: 사람이 읽는 결과 요약
