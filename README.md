# SimpleClaw

**확장 가능한 개인 비서 AI 에이전트** (Python)

SimpleClaw는 사용자의 일상 업무를 자율적으로 처리하는 AI 에이전트입니다. 여러 LLM을 유연하게 전환하고, 스킬과 레시피를 통해 기능을 확장하며, 텔레그램으로 언제 어디서든 소통할 수 있습니다. 모든 설정은 YAML 파일 하나로 관리됩니다.

## 주요 기능

| 기능 | 설명 |
|-----|------|
| **페르소나 시스템** | `AGENT.md`(성격), `USER.md`(사용자 정보), `MEMORY.md`(장기 기억)로 에이전트의 응답 스타일과 맥락을 정의합니다. |
| **다중 LLM 라우팅** | Claude, Gemini, ChatGPT, 외부 CLI 도구를 `config.yaml` 설정으로 자유롭게 전환합니다. |
| **스킬 실행** | SKILL.md로 정의된 도구(Python/Bash 스크립트)를 자동으로 발견하고 실행합니다. 메일 확인, 주식 조회, 맛집 검색 등. |
| **레시피** | YAML로 정의된 다단계 워크플로우를 변수 치환과 함께 자동 실행합니다. |
| **대화 기억** | 모든 대화를 SQLite에 저장하고, 최근 히스토리를 LLM 호출 시 자동 주입하여 맥락을 유지합니다. |
| **드리밍** | 심야에 대화 내역을 요약하여 `MEMORY.md`에 핵심만 보존합니다. 실행 전 `.bak` 백업을 생성합니다. |
| **백그라운드 데몬** | 5분 주기 Heartbeat 모니터링, Cron Job 스케줄링, 드리밍 자동 트리거를 지원합니다. |
| **서브 에이전트** | 복잡한 작업을 격리된 서브프로세스로 위임합니다. 최대 3개 동시 실행, 권한 스코프 주입. |
| **텔레그램 봇** | 화이트리스트 기반 접근 제어로 안전한 양방향 메시징을 제공합니다. |
| **Webhook 리스너** | Zapier, n8n 등 외부 서비스의 이벤트를 수신하는 REST 엔드포인트입니다. |
| **음성 (STT/TTS)** | OpenAI Whisper/TTS API를 통한 음성 입출력을 지원합니다. |
| **로깅 및 대시보드** | JSONL 실행 로그, 메트릭스 수집, 웹 대시보드로 시스템을 모니터링합니다. |

## 동작 방식

사용자가 메시지를 보내면 에이전트는 다음 파이프라인을 실행합니다:

```
사용자 메시지 수신
  |
  v
[1] 스킬 판단 -------- "이 질문에 스킬이 필요한가?"
  |                     LLM이 사용 가능한 스킬 목록을 읽고 판단
  |--- 불필요 ----------> [3]으로 이동
  |--- 필요 ------------> [2]로 이동
  |
[2] 스킬 실행 --------- 선택된 스크립트를 subprocess로 실행
  |                     결과 수집 (메일 목록, 주가 데이터, 검색 결과 등)
  |
[3] 응답 생성
  |   시스템 프롬프트 = 페르소나 (AGENT.md + USER.md + MEMORY.md)
  |                  + 사용 가능한 스킬 목록
  |                  + 스킬 실행 결과 (있는 경우)
  |   대화 히스토리   = SQLite에서 최근 N개 대화
  |                  + 현재 메시지
  |
  v
[4] 저장 및 응답 ------ DB에 저장, 사용자에게 응답 전달
```

## 프로젝트 구조

```
src/simpleclaw/
  persona/        # 페르소나 파싱 및 프롬프트 어셈블리
  llm/            # 다중 LLM 라우터 (Claude, Gemini, OpenAI, CLI)
  skills/         # 스킬 디스커버리, 실행 엔진, MCP 클라이언트
  recipes/        # YAML 레시피 로더 및 단계별 실행기
  memory/         # SQLite 대화 저장소, 드리밍 파이프라인
  daemon/         # Heartbeat 모니터, Cron 스케줄러, 대기 상태 관리
  agents/         # 서브 에이전트 스포너, 동시 실행 풀
  channels/       # 텔레그램 봇, Webhook 서버
  voice/          # STT (Whisper), TTS 프로세서
  logging/        # 구조화 로거, 메트릭스 수집기, 웹 대시보드
  agent.py        # 전체를 하나로 묶는 중앙 오케스트레이터
  config.py       # 통합 설정 로더
```

## 시작하기

### 사전 요구 사항

- Python 3.11 이상
- [uv](https://docs.astral.sh/uv/) (권장) 또는 pip

### 설치

```bash
git clone https://github.com/ingki3/simpleclaw.git
cd simpleclaw
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 설정

1. API 키를 `.env` 파일에 추가:

```bash
echo 'GOOGLE_API_KEY=your-gemini-key' >> .env
# 또는
echo 'ANTHROPIC_API_KEY=your-claude-key' >> .env
```

2. 페르소나 파일 생성:

```bash
mkdir -p .agent
```

`.agent/AGENT.md`:
```markdown
# Agent

You are SimpleClaw, a helpful personal assistant.
Respond in the same language the user writes in.
```

`.agent/USER.md`:
```markdown
# User

Name: 홍길동
Language: Korean
```

### 텔레그램 봇 실행

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)에게 `/newbot`으로 봇 생성
2. [@userinfobot](https://t.me/userinfobot)에서 본인 User ID 확인
3. 설정 추가:

```bash
echo 'TELEGRAM_BOT_TOKEN=발급받은-토큰' >> .env
```

`config.yaml`에 User ID 추가:
```yaml
telegram:
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  whitelist:
    user_ids: [본인-User-ID]
```

4. 실행:

```bash
python scripts/test_telegram.py
```

### 테스트 실행

```bash
pytest tests/ -v
```

307개 테스트 (단위, 통합, PRD 시나리오, 실제 스킬 시나리오) 포함.

## 스킬 시스템

스킬은 에이전트가 사용할 수 있는 독립적인 도구입니다. 각 스킬은 `SKILL.md` 파일로 정의됩니다.

**스킬 디렉토리:**
- 로컬 (프로젝트 전용): `.agent/skills/`
- 전역 (공유): `~/.agents/skills/`

동일한 이름의 스킬이 있으면 로컬이 전역을 우선합니다.

**스킬 구조 예시:**
```
~/.agents/skills/gmail-skill/
  SKILL.md          # 이름, 설명, 사용법 (bash 명령어 예시 포함)
  scripts/
    gmail.py        # 실행 스크립트
    venv/           # 격리된 의존성
```

**사용자 질문과 스킬 매핑 예시:**

| 사용자 질문 | 선택되는 스킬 | 실행 명령 |
|-----------|------------|---------|
| "읽지 않은 메일 확인해줘" | gmail-skill | `gmail.py search --query "is:unread"` |
| "내 일정 확인해봐" | google-calendar-skill | `gcal.py list --days 7` |
| "AAPL 주가 알려줘" | us-stock-skill | `us_stock.py info --symbol AAPL` |
| "여의도 맛집 찾아줘" | local-route-skill | `search_and_route.py search --query "여의도 맛집"` |
| "최신 AI 뉴스 검색해줘" | news-search-skill | `news_search.py --query "최신 AI 뉴스"` |

## 설정 파일

모든 설정은 `config.yaml`에서 관리합니다:

```yaml
llm:
  default: "gemini"                    # 기본 LLM 프로바이더
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key_env: "GOOGLE_API_KEY"
    claude:
      type: "api"
      model: "claude-sonnet-4-20250514"
      api_key_env: "ANTHROPIC_API_KEY"

agent:
  history_limit: 20                    # 프롬프트에 포함할 대화 수
  db_path: ".agent/conversations.db"

persona:
  token_budget: 4096                   # 페르소나에 할당할 최대 토큰

daemon:
  heartbeat_interval: 300              # Heartbeat 주기 (초)
  dreaming:
    overnight_hour: 3                  # 드리밍 시각 (03:00)
    idle_threshold: 7200               # 드리밍 조건: 유휴 시간 (초)

sub_agents:
  max_concurrent: 3                    # 서브 에이전트 동시 실행 제한

telegram:
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  whitelist:
    user_ids: []                       # 인가된 텔레그램 User ID
```

## 설계 원칙

프로젝트 전체에 적용되는 원칙입니다 (`.specify/memory/constitution.md` 참조):

1. **Python 전용** -- 코어 런타임에 다른 언어 의존성 없음
2. **경량 의존성** -- asyncio + APScheduler + SQLite만 사용, Docker/Celery/Redis 불필요
3. **설정 기반** -- 모든 동작을 `config.yaml`과 `.env`로 제어
4. **명시적 보안** -- 서브 에이전트 권한 스코프, 텔레그램 화이트리스트
5. **페르소나 무결성** -- AGENT.md/USER.md는 드리밍 외 읽기 전용
6. **격리된 확장** -- 스킬, 레시피, 서브 에이전트 모두 샌드박스 환경

## 개발 가이드

### 브랜치 전략

```
feature/xxx  -->  dev  -->  main
              PR         PR
```

`main`과 `dev` 모두 직접 push 불가, PR을 통해서만 merge됩니다.

### 기능 추가 방법

```bash
git checkout dev
git checkout -b feature/my-feature
# ... 개발 ...
git push -u origin feature/my-feature
# PR 생성: feature/my-feature -> dev
# 머지 후 PR 생성: dev -> main
```

## 라이선스

MIT
