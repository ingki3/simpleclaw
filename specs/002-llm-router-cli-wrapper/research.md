# Research: 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑

## 1. LLM SDK 선택

**Decision**: 각 프로바이더의 공식 Python SDK 사용
- Claude: `anthropic` 패키지
- ChatGPT: `openai` 패키지
- Gemini: `google-genai` 패키지

**Rationale**: 공식 SDK는 인증, 에러 처리, 타입 정의가 내장되어 있고, 직접 HTTP 호출보다 유지보수성이 높다.

**Alternatives considered**:
- `litellm`: 통합 라이브러리이나 추가 추상화 레이어로 Constitution II(경량 의존성) 위반 우려
- 직접 HTTP 호출: 인증 로직, 재시도 등을 직접 구현해야 하며 각 프로바이더 API 변경에 취약

## 2. 프로바이더 추상화 패턴

**Decision**: ABC(Abstract Base Class) 기반 Provider 패턴

**Rationale**: 각 프로바이더를 동일한 인터페이스로 추상화하여, router가 구체 프로바이더를 알 필요 없이 라우팅 가능. 새 프로바이더 추가 시 기존 코드 수정 없이 클래스 하나만 추가.

**Alternatives considered**:
- 함수 기반 디스패치: 단순하지만 공유 상태(API 키, 모델명) 관리 불편
- 플러그인 시스템: 현 단계에서는 과도한 복잡성

## 3. CLI 래핑 방식

**Decision**: `asyncio.create_subprocess_exec`로 비동기 서브프로세스 실행

**Rationale**: PRD 4.3절에서 asyncio 기반 구동을 명시. 비동기 서브프로세스는 메인 이벤트 루프를 블로킹하지 않으면서 CLI 도구의 응답을 대기할 수 있다.

**Alternatives considered**:
- `subprocess.run` (동기): 메인 루프 블로킹
- `concurrent.futures.ProcessPoolExecutor`: 불필요한 복잡성

## 4. 설정 구조

**Decision**: config.yaml에 `llm` 섹션 추가, .env에서 API 키 로드

**Rationale**: Constitution III(Configuration-Driven Flexibility) 준수. API 키는 민감 정보이므로 .env에 분리(Constitution IV).

```yaml
llm:
  default: "claude"
  providers:
    claude:
      type: "api"
      model: "claude-sonnet-4-20250514"
      api_key_env: "ANTHROPIC_API_KEY"
    openai:
      type: "api"
      model: "gpt-4o"
      api_key_env: "OPENAI_API_KEY"
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key_env: "GOOGLE_API_KEY"
    goose:
      type: "cli"
      command: "goose"
      args: ["run", "--model", "claude-sonnet-4-20250514"]
      timeout: 120
```

## 5. 에러 처리 전략

**Decision**: 재시도 없이 즉시 에러 반환, 구조화된 에러 타입

**Rationale**: 재시도 로직은 호출자(상위 레이어)의 책임. LLM 모듈은 단순 전달 계층으로 유지하여 복잡성을 최소화.
