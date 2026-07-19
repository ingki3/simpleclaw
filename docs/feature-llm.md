# 다중 LLM 라우팅

SimpleClaw는 여러 LLM endpoint를 지원합니다. 역할별 `llm.routes`가 의미 기반
backend alias를 선택하고, 각 backend가 wire `transport`와 endpoint `profile`을
명시합니다. 따라서 모델 교체는 호출 코드가 아니라 설정에서 수행합니다.

## 지원 프로바이더

| 프로바이더 | 모델 예시 | API 키 환경변수 |
|-----------|----------|----------------|
| Claude (Anthropic) | claude-sonnet-4-20250514 | `ANTHROPIC_API_KEY` |
| Gemini (Google) | gemini-3.1-flash-lite-preview | `GOOGLE_API_KEY` |
| GPT-4o (OpenAI) | gpt-4o | `OPENAI_API_KEY` |
| Gemini OpenAI-compatible (A/B) | gemini-2.5-flash | `GOOGLE_API_KEY` |

## 설정

```yaml
llm:
  routes:
    default: {primary: chat_primary, retry: chat_fallback}
    turn_analysis: {primary: analysis_fast, retry: analysis_safe}
    multimodal: {primary: vision_native}
  providers:
    chat_primary:
      type: "api"
      model: "claude-sonnet-4-20250514"
      transport: "anthropic"
      profile: "anthropic"
      api_key_env: "ANTHROPIC_API_KEY"
    chat_fallback:
      type: "api"
      model: "gpt-4o"
      transport: "openai_chat"
      profile: "openai"
      api_key_env: "OPENAI_API_KEY"
    analysis_fast:
      type: "api"
      model: "gemini-3.1-flash-lite-preview"
      transport: "gemini"
      profile: "gemini"
      api_key_env: "GOOGLE_API_KEY"
    analysis_safe:
      type: "api"
      model: "gpt-4o"
      transport: "openai_chat"
      profile: "openai"
      api_key_env: "OPENAI_API_KEY"
    vision_native:
      type: "api"
      model: "gemini-3.1-flash-lite-preview"
      transport: "gemini"
      profile: "gemini"
      api_key_env: "GOOGLE_API_KEY"
    # opt-in only: native Gemini replacement requires parity evidence and operator approval
    # gemini_openai_ab:
    #   type: "api"
    #   model: "gemini-2.5-flash"
    #   transport: "openai_chat"
    #   profile: "gemini-openai"
    #   api_key_env: "GOOGLE_API_KEY"
    #   base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
```

## 동작 방식

1. `create_router(config_path)`가 route/backend/transport/profile을 초기화합니다.
2. API 키가 설정된 backend만 활성화되며, route는 primary와 compatible retry를 사용합니다.
3. attachment 요청은 `multimodal` route가 있으면 그 route로 갑니다.
4. TurnAnalysis 등 역할별 호출부는 provider/model을 직접 선택하지 않고 route name만 전달합니다.

## Migration and operations

- Legacy `llm.default`/`fallback`/`multimodal`과 `agent.turn_analysis` selector는 한 release 동안 warning과 함께 route로 정규화됩니다. 새 설정은 `llm.routes`와 explicit `transport`/`profile`을 사용하세요.
- `llm.routes`, backend model, transport, profile, or credential 변경은 service restart가 필요합니다. LLM config hot reload는 지원하지 않습니다.
- `gemini-openai`는 `openai_chat` transport를 공유하는 opt-in A/B profile입니다. schema/text 외 tool replay, reasoning, image parity가 검증되기 전에는 native Gemini route를 유지합니다.
- OpenAI Responses API는 Chat Completions와 별도 transport입니다. `openai_responses`를 설정하면 구현 전에는 actionable 오류가 발생합니다.
- Live provider/default 변경 전에는 config backup → validation → provider smoke → restart → health/channel/scheduler/dashboard/redacted-log 확인 순서를 따르며, native/default 전환은 운영자 승인 사항입니다.

## LLM 요청 모델

```python
@dataclass
class LLMRequest:
    system_prompt: str       # 시스템 프롬프트 (페르소나 + 스킬 목록)
    user_message: str        # 사용자 메시지
    messages: list[dict]     # 대화 히스토리 (role + content)
```

## 에러 처리

- `LLMAuthError` — API 키 인증 실패
- `LLMTimeoutError` — 응답 타임아웃
- `LLMProviderError` — 프로바이더 내부 오류

LLM 에러 발생 시 사용자에게 "오류가 발생했습니다" 메시지를 반환합니다.

## 관련 파일

- `src/simpleclaw/llm/router.py` — 라우터 팩토리 및 라우팅 로직
- `src/simpleclaw/llm/models.py` — LLMRequest, LLMResponse 모델
- `src/simpleclaw/llm/providers/` — 프로바이더별 구현
- `docs/architecture/llm-transports.md` — transport/profile 경계와 Responses 확장 계약
