# Quickstart: LLM 라우터 및 CLI 래퍼

## 사전 준비

1. Python 3.11+ 및 프로젝트 의존성 설치
2. `.env` 파일에 API 키 설정:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   OPENAI_API_KEY=sk-...
   GOOGLE_API_KEY=AI...
   ```

## 기본 사용

### config.yaml 설정

```yaml
llm:
  default: "claude"
  providers:
    claude:
      type: "api"
      model: "claude-sonnet-4-20250514"
      api_key_env: "ANTHROPIC_API_KEY"
```

### 코드에서 사용

```python
import asyncio
from simpleclaw.llm import create_router, LLMRequest

async def main():
    router = create_router("config.yaml")
    
    request = LLMRequest(
        system_prompt="You are a helpful assistant.",
        user_message="Hello, who are you?"
    )
    
    response = await router.send(request)
    print(response.text)

asyncio.run(main())
```

### 페르소나와 통합

```python
from simpleclaw.persona import resolve_persona_files, assemble_prompt
from simpleclaw.config import load_persona_config

config = load_persona_config("config.yaml")
files = resolve_persona_files(config["local_dir"], config["global_dir"])
assembly = assemble_prompt(files, config["token_budget"])

request = LLMRequest(
    system_prompt=assembly.assembled_text,
    user_message="오늘 일정을 알려줘"
)
response = await router.send(request)
```

## 검증

```bash
pytest tests/ -v
```
