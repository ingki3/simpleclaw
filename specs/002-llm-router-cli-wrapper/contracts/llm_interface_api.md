# API Contract: LLM 라우터

## Public Interface

### `create_router(config_path) -> LLMRouter`
config.yaml에서 LLM 설정을 로드하고, 등록된 프로바이더로 라우터를 초기화한다.

**Parameters**:
- `config_path` (str | Path): config.yaml 파일 경로

**Returns**: `LLMRouter` 인스턴스

### `LLMRouter.send(request) -> LLMResponse` (async)
요청을 적절한 백엔드로 라우팅하여 LLM 응답을 반환한다.

**Parameters**:
- `request` (LLMRequest): 시스템 프롬프트, 사용자 메시지, 선택적 백엔드명

**Returns**: `LLMResponse` — 응답 텍스트, 백엔드 정보, 토큰 사용량

**Errors**:
- `LLMConfigError`: 설정 파일 오류 또는 백엔드 미등록
- `LLMAuthError`: API 키 누락 또는 인증 실패
- `LLMProviderError`: API 호출 실패 (네트워크 오류 등)
- `LLMTimeoutError`: CLI 타임아웃 초과
- `LLMCLINotFoundError`: CLI 도구 미설치

### `LLMRouter.list_backends() -> list[str]`
등록된 모든 백엔드 이름 목록을 반환한다.

### `LLMRouter.get_default_backend() -> str`
기본 백엔드 이름을 반환한다.
