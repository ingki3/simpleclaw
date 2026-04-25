# 설치 및 실행

## 요구사항

- Python 3.11+
- pip 또는 uv (패키지 관리)

## 설치

```bash
# 저장소 클론
git clone https://github.com/your-org/SimpleClaw.git
cd SimpleClaw

# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate

# 의존성 설치
pip install -e .
```

## 환경 설정

### 1. API 키 설정

프로젝트 루트에 `.env` 파일을 생성합니다:

```env
# LLM 프로바이더 (최소 하나 필요)
GOOGLE_API_KEY=your-gemini-api-key
ANTHROPIC_API_KEY=your-claude-api-key
OPENAI_API_KEY=your-openai-api-key

# 텔레그램 봇
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
```

### 2. config.yaml 설정

주요 설정 항목:

```yaml
# 기본 LLM 선택 (claude, gemini, openai)
llm:
  default: "gemini"

# 에이전트 동작
agent:
  history_limit: 20          # 대화 히스토리 최대 개수
  max_tool_iterations: 5     # 멀티턴 도구 최대 반복
  workspace_dir: ".agent/workspace"  # 스킬 파일 출력 디렉토리

# 데몬 설정
daemon:
  dreaming:
    model: "gemini"          # 드리밍에 사용할 LLM (선택)

# 텔레그램 화이트리스트
telegram:
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  whitelist:
    user_ids: [YOUR_TELEGRAM_USER_ID]
```

### 3. 페르소나 설정

`.agent/AGENT.md`를 편집하여 에이전트의 성격을 정의합니다:

```markdown
# SimpleClaw Agent

You are **SimpleClaw**, a personal assistant AI agent.

## Core Behavior
- You are helpful, concise, and friendly.
- You respond in the same language the user writes in.
```

`.agent/USER.md`에 사용자 정보를 설정합니다:

```markdown
# User Profile

## Preferences
- Primary language: Korean (한국어)
- Timezone: Asia/Seoul (KST, UTC+9)
```

## 실행

### 텔레그램 봇 실행 (포그라운드)

```bash
.venv/bin/python scripts/test_telegram.py
```

### 백그라운드 실행

```bash
nohup .venv/bin/python scripts/test_telegram.py > .agent/bot.log 2>&1 &
```

### 로그 확인

```bash
tail -f .agent/bot.log
```

### 봇 중지

```bash
pgrep -f "test_telegram.py" | xargs kill
```

## 테스트

```bash
# 전체 테스트
.venv/bin/python -m pytest tests/

# 단위 테스트만
.venv/bin/python -m pytest tests/unit/

# 특정 모듈 테스트
.venv/bin/python -m pytest tests/unit/test_agent.py -v
```

## 디렉토리 초기화

첫 실행 시 자동으로 생성되는 파일들:

```
.agent/
├── conversations.db    # 대화 히스토리 DB
├── daemon.db          # Cron 작업 DB
├── workspace/         # 스킬 파일 출력 디렉토리
├── HEARTBEAT.md       # 데몬 상태
└── bot.log            # 봇 로그 (백그라운드 실행 시)
```
