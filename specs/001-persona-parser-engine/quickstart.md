# Quickstart: 페르소나 설정 파싱 엔진

## 사전 준비

1. Python 3.11+ 설치
2. 프로젝트 의존성 설치:
   ```bash
   pip install markdown-it-py tiktoken pyyaml
   ```

## 기본 사용

### 1. config.yaml 설정

프로젝트 루트에 `config.yaml`을 생성한다:

```yaml
persona:
  token_budget: 4096
  local_dir: ".agent"
  global_dir: "~/.agents/main"
  files:
    - name: "AGENT.md"
      type: "agent"
    - name: "USER.md"
      type: "user"
    - name: "MEMORY.md"
      type: "memory"
```

### 2. 페르소나 파일 배치

`.agent/` 디렉토리에 마크다운 파일을 배치한다:

```
.agent/
├── AGENT.md    # 에이전트 역할, 톤앤매너 정의
├── USER.md     # 사용자 정보, 선호도
└── MEMORY.md   # 핵심 기억 요약
```

### 3. 코드에서 사용

```python
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.config import load_persona_config

# 설정 로드
config = load_persona_config("config.yaml")

# 파일 탐색 및 파싱
files = resolve_persona_files(
    local_dir=config["local_dir"],
    global_dir=config["global_dir"]
)

# System Prompt 조립
result = assemble_prompt(files, token_budget=config["token_budget"])

print(result.assembled_text)
print(f"Tokens: {result.token_count}, Truncated: {result.was_truncated}")
```

## 검증

```bash
pytest tests/ -v
```

예상 결과: 파싱, 경로 탐색, 프롬프트 조립 테스트가 모두 통과.
