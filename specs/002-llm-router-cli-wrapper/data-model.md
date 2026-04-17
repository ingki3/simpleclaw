# Data Model: 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑

## Entities

### BackendType (Enum)
| Value | Description |
|-------|-------------|
| API   | HTTP API 기반 프로바이더 |
| CLI   | 서브프로세스 CLI 기반 프로바이더 |

### LLMBackend
| Attribute | Type | Description |
|-----------|------|-------------|
| name | str | 프로바이더 식별자 (예: "claude", "openai", "goose") |
| backend_type | BackendType | API 또는 CLI |
| model | str | 사용할 모델명 (예: "claude-sonnet-4-20250514") |
| api_key_env | str or None | API 키를 저장한 환경 변수명 (API 타입만 해당) |
| command | str or None | CLI 실행 명령어 (CLI 타입만 해당) |
| args | list[str] | CLI 추가 인자 (CLI 타입만 해당) |
| timeout | int | CLI 타임아웃 초 (기본 120) |

### LLMRequest
| Attribute | Type | Description |
|-----------|------|-------------|
| system_prompt | str | System Prompt 텍스트 (PromptAssembly.assembled_text) |
| user_message | str | 사용자 입력 메시지 |
| backend_name | str or None | 특정 백엔드 지정 (None이면 default 사용) |

### LLMResponse
| Attribute | Type | Description |
|-----------|------|-------------|
| text | str | LLM 응답 텍스트 |
| backend_name | str | 사용된 백엔드 이름 |
| model | str | 사용된 모델명 |
| usage | dict or None | 토큰 사용량 (input_tokens, output_tokens) — API만 해당 |

## Relationships

```
LLMRequest → LLMBackend (1:1, backend_name으로 매핑)
LLMBackend → LLMResponse (1:1, 호출 결과)
```

## State Transitions

없음. 모든 개체는 stateless 요청-응답 단위로 생성/소멸.
