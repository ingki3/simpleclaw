# 다중 LLM 라우팅

SimpleClaw는 여러 LLM 프로바이더를 지원하며, `config.yaml`에서 기본 프로바이더를 선택할 수 있습니다.

## 지원 프로바이더

| 프로바이더 | 모델 예시 | API 키 환경변수 |
|-----------|----------|----------------|
| Claude (Anthropic) | claude-sonnet-4-20250514 | `ANTHROPIC_API_KEY` |
| Gemini (Google) | gemini-3.1-flash-lite-preview | `GOOGLE_API_KEY` |
| GPT-4o (OpenAI) | gpt-4o | `OPENAI_API_KEY` |

## 설정

```yaml
llm:
  default: "gemini"    # 기본 프로바이더 선택
  providers:
    claude:
      type: "api"
      model: "claude-sonnet-4-20250514"
      api_key_env: "ANTHROPIC_API_KEY"
    gemini:
      type: "api"
      model: "gemini-3.1-flash-lite-preview"
      api_key_env: "GOOGLE_API_KEY"
    openai:
      type: "api"
      model: "gpt-4o"
      api_key_env: "OPENAI_API_KEY"
```

## 동작 방식

1. `create_router(config_path)`로 라우터 초기화
2. API 키가 설정된 프로바이더만 활성화 (누락 시 경고 로그 출력)
3. `default`로 지정된 프로바이더로 모든 요청 라우팅
4. 스킬 라우팅과 최종 응답 생성 모두 동일한 프로바이더 사용

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
