# 스킬 시스템

스킬은 에이전트가 사용할 수 있는 외부 도구입니다. `SKILL.md` 파일로 정의하며, LLM이 사용자 메시지를 분석하여 적절한 스킬을 자동으로 선택합니다.

## 디렉토리 구조

```
.agent/skills/          ← 로컬 스킬 (프로젝트별)
~/.agents/skills/       ← 전역 스킬 (모든 프로젝트 공유)
```

동일 이름 스킬이 있으면 로컬이 전역을 우선합니다.

## SKILL.md 작성

### YAML 프론트매터 형식 (권장)

```yaml
---
name: "gmail-skill"
description: "Gmail에서 메일을 검색하고 읽는 스킬"
argument-hint: "검색어 또는 메일 ID"
user-invocable: true
---

## Usage

\`\`\`bash
python run.py search --query "from:boss subject:urgent"
python run.py read --id MESSAGE_ID
\`\`\`

## Trigger

사용자가 메일 확인, 이메일 검색, 읽지 않은 메일 등을 요청할 때
```

### 마크다운 헤딩 형식 (레거시)

```markdown
# gmail-skill

Gmail에서 메일을 검색하고 읽는 스킬

## Script

Target: `run.py`

## Trigger

사용자가 메일 관련 요청을 할 때
```

## 스킬 실행 흐름 (ReAct 통합)

스킬 실행은 ReAct(Reasoning + Acting) 루프 안에서 이루어집니다:

```
사용자: "읽지 않은 메일 확인해줘"
  ↓
ReAct Step 1:
  Thought: "메일을 확인하려면 gmail-skill을 사용해야 한다"
  Action: {"skill_name": "gmail-skill", "command": "python run.py search --query is:unread"}
  ↓
보안 검사 (CommandGuard)
  ↓
Python 경로 자동 해석 (_fix_python_path)
  ↓
스킬 실행 (async subprocess, cwd=workspace, timeout 60초)
  ↓
Observation: 스킬 실행 결과
  ↓
ReAct Step 2:
  Thought: "메일 목록을 받았으니 사용자에게 정리해서 알려줄 수 있다"
  Answer: 자연어 응답 생성
```

### 스마트 Python 경로 해석

LLM이 `python script.py`와 같이 bare python을 사용한 명령을 생성하면, `_fix_python_path()`가 자동으로 스크립트 근처의 가상환경(venv/.venv)을 탐색하여 올바른 Python 인터프리터 경로로 대체합니다:

```
python run.py → /path/to/skill/.venv/bin/python run.py
```

탐색 우선순위: `스크립트 디렉토리/venv` → `상위 디렉토리/venv` → `스크립트 디렉토리/.venv` → `상위 디렉토리/.venv`

### Workspace 디렉토리

스킬 명령은 workspace 디렉토리(기본 `.agent/workspace`)를 작업 디렉토리(`cwd`)로 사용합니다. 환경변수 `AGENT_WORKSPACE`로 절대 경로가 주입되어, 스킬 스크립트에서 파일 출력 위치로 활용할 수 있습니다.

## 멀티턴 실행 (ReAct)

하나의 메시지에서 여러 스킬을 순차 호출할 수 있습니다 (최대 `max_tool_iterations` 회, 기본 5). LLM이 각 단계에서 Thought로 추론하고, Action으로 스킬을 호출하며, Observation에서 결과를 분석합니다:

```
사용자: "메일 확인하고 오늘 일정도 알려줘"
  → [Thought] "먼저 메일을 확인해야 한다"
  → [Action] gmail-skill → [Observation] 메일 결과
  → [Thought] "이제 일정도 확인해야 한다"
  → [Action] google-calendar-skill → [Observation] 일정 결과
  → [Thought] "두 결과를 종합할 수 있다"
  → [Answer] 두 결과를 종합하여 응답
```

## MCP 통합

Model Context Protocol(MCP) 서버의 도구도 스킬과 함께 사용할 수 있습니다:

```yaml
# config.yaml
mcp:
  servers:
    my-tools:
      command: "npx my-mcp-server"
```

스킬과 MCP 도구의 이름이 같으면 스킬이 우선합니다.

## 스킬 개발 가이드

### 1. 디렉토리 생성

```bash
mkdir -p .agent/skills/my-skill
```

### 2. SKILL.md 작성

```yaml
---
name: "my-skill"
description: "커스텀 스킬 설명"
user-invocable: true
---

## Usage
\`\`\`bash
python run.py --input "데이터"
\`\`\`
```

### 3. 실행 스크립트 작성

```python
# .agent/skills/my-skill/run.py
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
args = parser.parse_args()

print(f"처리 결과: {args.input}")
```

### 4. 즉시 사용

스킬 파일을 추가하면 다음 메시지부터 자동으로 발견되어 사용 가능합니다.

## 설정

```yaml
skills:
  local_dir: ".agent/skills"
  global_dir: "~/.agents/skills"
  execution_timeout: 60     # 초 단위 타임아웃
```

## 관련 파일

- `src/simpleclaw/skills/discovery.py` — 디스커버리 및 SKILL.md 파싱
- `src/simpleclaw/skills/executor.py` — 비동기 스킬 실행
- `src/simpleclaw/skills/mcp_client.py` — MCP 클라이언트
- `src/simpleclaw/skills/models.py` — SkillDefinition, SkillResult 모델
