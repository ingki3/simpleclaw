# BIZ-444 Spike — Vertex AI Gemini 프로바이더 도입 검토

- 작성일: 2026-07-16
- 상태: **spike 완료 — 도입 권고 (opt-in, 기본 백엔드 변경 없음)**
- 관련 코드: `src/simpleclaw/llm/providers/vertex_gemini.py`, `src/simpleclaw/llm/router.py`, `tests/unit/test_vertex_gemini_provider.py`

## 1. 배경

Hermes v0.18.0 은 static API key 대신 GCP service account/ADC 기반 short-lived
OAuth token 을 자동 갱신하는 Vertex AI Gemini provider 를 도입했다. SimpleClaw 의
`llm.providers.gemini` 는 현재 Google AI Studio(Developer API) static API key
방식이다. 당장 장애는 없으나 다음 관점에서 Vertex 경로가 장기적으로 유리하다.

- **키 회전**: static API key 는 유출 시 수동 폐기/재발급이 필요. Vertex 는
  1시간 수명의 OAuth access token 을 자동 재발급하므로 유출 창이 짧고,
  SA 키 교체·ADC 재로그인이 프로세스 재시작 없이 반영된다.
- **조직 IAM**: 프로젝트/서비스 계정 단위 권한(roles/aiplatform.user), audit
  logging, 조직 정책(VPC-SC 등)을 GCP IAM 으로 일원화할 수 있다.
- **quota 관리**: 프로젝트 단위 quota/billing 이 분리된다.

## 2. 구현 방식 비교 (spike 결과)

| 옵션 | 내용 | 판정 |
|---|---|---|
| **(A) google-genai SDK `vertexai=True`** | 기존 `GeminiProvider` 가 쓰는 동일 SDK(설치본 2.10.0)가 Vertex 백엔드를 네이티브 지원. `genai.Client(vertexai=True, project=..., location=..., credentials=...)` 한 줄 차이 | **채택** |
| (B) Vertex OpenAI-compatible endpoint | `OpenAIProvider` + base_url 재사용 가능하나, 토큰 갱신을 직접 구현해야 하고(OpenAI SDK 는 정적 api_key 가정) tool call/structured output/thought_signature 가 OpenAI 스키마로 열화됨 | 기각 |
| (C) `google-cloud-aiplatform` SDK | 별도 의존성 추가 + 요청/응답 변환 계층 전면 재작성 필요. google-genai 가 이미 공식 통합 SDK | 기각 |

**핵심 발견**: 옵션 (A)는 요청 변환·응답 파싱·Native Function Calling·structured
output·스트리밍·`thought_signature` 보존 코드가 `GeminiProvider` 와 **완전히
동일한 SDK 코드 경로**다. 어댑터는 클라이언트 생성만 바꾸는 얇은 서브클래스로
충분하며(약 140줄, 대부분 docstring), 신규 런타임 의존성이 **0** 이다
(`google-auth` 는 이미 `google-genai` 의 전이 의존성으로 설치됨).

## 3. 인증/토큰 refresh 설계

### 3.1 인증 경로 우선순위 (`VertexGeminiProvider.__init__`)

1. **`credentials_path`** — service account JSON. `google.oauth2.service_account`
   로 로드(scope `cloud-platform`). `project` 미지정 시 SA JSON 의 `project_id`
   를 사용해 SDK 의 불필요한 ADC 재조회를 차단한다.
2. **`project`(+선택 `location`)** — ADC 경로. `gcloud auth application-default
   login` 또는 `GOOGLE_APPLICATION_CREDENTIALS` 환경변수.
3. **`api_key`** — Vertex **express mode**. SDK 가 project/location 과 api_key 를
   상호 배타(ValueError)로 강제하므로, project 가 있으면 api_key 는 무시한다
   (IAM 경로 우선). 라우터의 공통 생성 규약(`api_key` 인자) 호환용.
4. **전부 없음** — SDK 가 ADC 에서 credentials 와 project 를 자동 발견.
   실패(`DefaultCredentialsError`)는 `LLMAuthError` 로 매핑되어 라우터가 해당
   프로바이더만 skip 한다 (부분 가용성 정책 유지).

`location` 기본값은 `"global"` — 리전 고정 없이 Gemini 모델을 서빙하는
엔드포인트로, 리전 quota 관리가 필요 없는 기본 구성에 적합하다.

### 3.2 토큰 mint/refresh — SimpleClaw 는 직접 구현하지 않는다

google-genai SDK(2.10.0) 내부 확인 결과(`_api_client.BaseApiClient`):

- access token 발급/만료 전 재발급은 google-auth 의 authorized session 이
  **매 요청 시점에** 수행한다. 만료 임박 시 자동 refresh.
- 동시 코루틴의 credential 접근은 SDK 내부 sync/async auth lock 으로 보호된다.
- 명시 `project`+`location` 을 주면 클라이언트 **초기화 시점에는** ADC 조회가
  없다(첫 요청에서 lazy 조회) — 초기화 실패와 런타임 인증 실패가 분리된다.

따라서 SimpleClaw 프로바이더는 **토큰 상태를 일절 보유하지 않는다**. 이는
의도된 계약이다: 키 회전(SA 키 교체, ADC 재로그인)이 봇 재시작 없이 다음
요청부터 반영되고, 토큰 캐시 오염·시크릿 로그 유출 표면이 생기지 않는다.

### 3.3 인증 에러 매핑

`GeminiProvider` 의 이름 기반 판별("auth"/"permission" in 예외 타입명)은
google-auth 예외(`RefreshError`, `DefaultCredentialsError`, `TransportError`)를
놓친다 — 토큰 refresh 실패가 일반 `LLMProviderError` 로 오분류되면 호출 측이
무의미한 재시도를 반복한다. 이를 위해 에러 매핑을 `_map_provider_error()` 훅으로
분리하고(기존 동작 불변), Vertex 프로바이더는 `isinstance(e, GoogleAuthError)`
를 추가로 `LLMAuthError` 로 분류한다.

## 4. 기능 호환성 분석 (DoD: tool call/structured output)

| 기능 | 호환성 | 근거 |
|---|---|---|
| Native Function Calling (BIZ-249 id 매칭 포함) | 동일 | `_convert_tools`/`_convert_messages`/응답 파싱 전부 상속 — 동일 `types.FunctionDeclaration` 스키마 |
| Structured output (BIZ-427/430) | 동일 | `GenerateContentConfig.response_mime_type/response_schema` 는 Vertex 백엔드에서도 동일 필드. required-스트리밍 거부 정책도 상속 |
| 스트리밍 (BIZ-259/284) | 동일 | `generate_content_stream` 동일 API. `thought_signature` 보존 로직 상속 |
| 멀티모달 inline bytes 첨부 | 동일 | `types.Part.from_bytes` 동일 |
| max_tokens (BIZ-297) | 동일 | config 매핑 상속 |
| system_blocks 평탄화 (BIZ-252) | 동일 | prompt caching 마커 없음 — Gemini 와 동일 정책 |
| 모델 ID | 주의 | Vertex 는 `gemini-3.5-flash` 형태 그대로 사용 가능. 단, 프로젝트별 모델 허용 목록/리전 가용성은 GCP 콘솔에서 확인 필요 |

## 5. config 설계

`config_sections/llm.py` 는 provider 블록의 미인식 키를 그대로 통과시키므로
**config 로더 변경이 필요 없다**. 라우터(`create_router`)가 프로바이더 클래스의
`EXTRA_CONFIG_KEYS` 선언(`project`, `location`, `credentials_path`)에 있는 키만
골라 생성자에 전달한다 — 선언 없는 기존 프로바이더는 시그니처 그대로(회귀 0).

```yaml
llm:
  providers:
    vertex_gemini:
      type: "api"
      model: "gemini-3.5-flash"
      project: "my-gcp-project"      # 생략 시 SA JSON/ADC 에서 자동 발견
      location: "global"             # 생략 시 "global"
      credentials_path: "~/.config/simpleclaw/vertex-sa.json"  # 생략 시 ADC
```

- 레지스트리 매칭은 provider **이름 기반**이므로 config 키는 `vertex_gemini`
  로 고정한다.
- `credentials_path` 는 시크릿이 아닌 **경로**이므로 시크릿 참조 문법
  (`env:`/`keyring:`/`file:`) 대상이 아니다. SA JSON 자체는 repo 밖
  (`~/.config/...`)에 두고 절대 커밋하지 않는다 (out-of-scope 항목 준수).

## 6. 결정 및 롤아웃

**권고: 도입한다. 단 opt-in 으로만.**

- 어댑터+테스트가 이번 spike 에서 함께 구현되었고, config 에 `vertex_gemini`
  블록을 추가하지 않는 한 **런타임 동작 변화가 전혀 없다** (레지스트리 등록은
  inert — 라우터는 config 에 존재하는 provider 만 초기화).
- **live 기본 백엔드(`llm.default`)는 변경하지 않는다.** 전환은 운영자가 GCP
  프로젝트/IAM 준비 후 별도 승인·staging smoke 를 거쳐 결정한다 (DoD 3항).

### 운영자 전환 절차 (승인 후, implementation phase)

1. GCP 프로젝트에 Vertex AI API 활성화, SA 에 `roles/aiplatform.user` 부여
   (또는 운영 머신에서 `gcloud auth application-default login`).
2. live `config.yaml` 에 `vertex_gemini` 블록 추가 (default 는 그대로).
3. staging smoke: `backend_name="vertex_gemini"` 지정 요청으로 텍스트/tool
   call/structured output 3종 확인.
4. 만족 시 `llm.default: vertex_gemini` 전환 + drain restart(BIZ-442 절차).

## 7. 테스트

- `tests/unit/test_vertex_gemini_provider.py` (17개): 인증 경로 선택(ADC/SA/
  express/자동 발견), SA project_id fallback, credentials 무가공 위임(토큰
  refresh 위임 계약), 초기화 실패 fail-fast, `RefreshError`→`LLMAuthError`
  매핑, structured output config 상속, 라우터 등록/extra key 전달/실패 시
  부분 가용성.
- live smoke 는 GCP 자격증명이 필요하므로 운영자 환경에서만 수행 (6절 절차).

## 8. 남은 리스크 / 후속 과제

- **express mode 는 편의 경로**: IAM 이점이 없으므로 문서상 권장하지 않음.
- **리전 quota**: `global` 이 아닌 특정 리전을 쓰면 모델별 리전 가용성 확인
  필요 — 운영자 전환 절차에 포함.
- **비용 모니터링**: Vertex 는 GCP billing 으로 청구 — 전환 시 기존 AI Studio
  quota 대시보드와 별도로 봐야 한다.
- Developer API 전용 기능(일부 실험 모델의 선공개 등)이 Vertex 에 늦게 오는
  경우가 있어, `gemini`(API key) 백엔드는 fallback 으로 당분간 유지한다.
