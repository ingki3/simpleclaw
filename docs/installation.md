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

### 개발 트리 vs 운영 트리 분리 (필수)

`scripts/run_bot.py` 는 **체크아웃된 working tree 의 파일을 그대로 import** 한다. 즉 운영 봇을 띄운 디렉터리의 git HEAD 가 곧 운영 코드의 버전이다. 개발용 feature 브랜치 위에서 봇을 실행하면 그 브랜치가 머지되기 전까지는 `dev` / `main` 에 들어간 핫픽스가 운영에 자동 반영되지 않는다 (BIZ-165 사고 클래스).

따라서 **개발 트리와 운영 트리를 물리적으로 분리**한다.

| 구분 | 경로 (표준) | 추적 브랜치 | 용도 |
|------|------|------|------|
| 개발 트리 | `~/Dev/SimpleClaw` | feature/* | 코드 작업·테스트·PR |
| 운영 트리 | `~/Dev/SimpleClaw-prod` | `origin/main` | 봇 데몬 상주 |

#### 운영 트리 최초 생성

```bash
# 개발 트리에서 추가 worktree 로 운영 트리를 만든다.
# detach + origin/main 으로 체크아웃해 feature 브랜치 전환 사고를 차단.
git -C ~/Dev/SimpleClaw worktree add --detach ~/Dev/SimpleClaw-prod origin/main

# 운영 트리 전용 가상환경 + 의존성
cd ~/Dev/SimpleClaw-prod
python -m venv .venv
.venv/bin/pip install -e .
```

데이터(대화 DB·페르소나·일정 등)는 `~/.simpleclaw/` 에 보관되어 worktree 와 독립이다 (BIZ-133). 따라서 운영 트리와 개발 트리는 같은 데이터를 공유하며, 트리 분리만으로 데이터 손실 위험은 없다.

> 주의 — `scripts/run_bot.py` 는 시작 시 `pgrep -f "run_bot.py"` 로 **cwd 와 관계없이 다른 `run_bot.py` 프로세스를 모두 SIGTERM** 한다 (Telegram 409 Conflict 방지). 개발 트리에서 임시로 봇을 띄우는 순간 운영 봇이 죽고 그 자리에 개발 코드가 올라가므로, **운영 중에는 개발 트리에서 `run_bot.py` 를 실행하지 말 것**. 디버깅이 필요하면 별도 봇 토큰을 쓰거나 운영 봇을 명시적으로 종료한 뒤 재기동한다.

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
  max_tool_iterations: 15    # 멀티턴 도구 최대 반복
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

운영 봇은 **반드시 운영 트리 (`~/Dev/SimpleClaw-prod`)에서** 실행한다. 개발 트리(`~/Dev/SimpleClaw`)에서 띄우면 미머지 feature 브랜치 코드가 그대로 운영에 올라간다.

### 텔레그램 봇 실행 (포그라운드)

```bash
cd ~/Dev/SimpleClaw-prod
.venv/bin/python scripts/run_bot.py
```

### 백그라운드 실행

```bash
cd ~/Dev/SimpleClaw-prod
nohup .venv/bin/python scripts/run_bot.py > ~/.simpleclaw/bot.log 2>&1 &
```

> 로그를 `~/.simpleclaw/bot.log` 로 두는 이유 — 운영 트리를 `git reset --hard` 하거나 다른 worktree 로 교체해도 로그가 보존된다. 트리 내부 `.agent/bot.log` 는 BIZ-140 정리 대상이므로 신규 운영에서는 사용하지 않는다.

### 로그 확인

```bash
tail -f ~/.simpleclaw/bot.log
```

### 봇 중지

```bash
pgrep -f "run_bot.py" | xargs kill
```

### 운영 트리 갱신 (릴리스 머지 직후 필수)

`dev → main` 릴리스 PR 이 머지된 직후, 운영자는 반드시 아래 시퀀스를 실행해 운영 트리를 최신 `origin/main` 으로 끌어올린다. 이 단계를 빠뜨리면 `dev` 의 핫픽스가 운영 봇에 반영되지 않아 BIZ-165 류 침묵 사고가 재발한다.

```bash
# 1) 최신 원격 가져오기
git -C ~/Dev/SimpleClaw-prod fetch origin

# 2) 운영 트리를 origin/main 에 강제 정렬 (detach 상태이므로 reset 안전)
git -C ~/Dev/SimpleClaw-prod reset --hard origin/main

# 3) 의존성이 바뀌었을 수 있으므로 재설치
~/Dev/SimpleClaw-prod/.venv/bin/pip install -e ~/Dev/SimpleClaw-prod

# 4) 봇 재기동
pkill -f "run_bot.py" || true
cd ~/Dev/SimpleClaw-prod && nohup .venv/bin/python scripts/run_bot.py > ~/.simpleclaw/bot.log 2>&1 &

# 5) 운영 트리가 올바른 커밋에서 돌고 있는지 검증
sleep 3
PID=$(pgrep -f "run_bot.py" | head -1)
lsof -p "$PID" | awk '$4=="cwd"{print "cwd:", $NF}'
git -C "$(lsof -p "$PID" | awk '$4=="cwd"{print $NF}')" log -1 --oneline
```

5번 출력의 `cwd:` 가 `~/Dev/SimpleClaw-prod` 를 가리키고 마지막 커밋이 방금 머지된 릴리스 커밋과 일치하면 성공.

#### 운영 트리가 dev 핫픽스를 미리 받아야 할 때

`dev` 에 침묵·크래시 등 사용자 영향 핫픽스가 머지됐는데 `main` 릴리스가 즉시 잡히지 않은 상황에서는, 위 시퀀스의 `origin/main` 을 `origin/dev` 로 잠시 바꿔 운영 트리를 일시적으로 `dev` HEAD 에 정렬한다. 다음 정상 릴리스가 머지되면 다시 `origin/main` 으로 복귀.

### 릴리스 후 점검 체크리스트

- [ ] `dev → main` PR 머지 완료
- [ ] 운영 트리 fetch → `git reset --hard origin/main` 실행
- [ ] `pip install -e .` 재실행 (pyproject 변경 시)
- [ ] 운영 봇 재기동 (`pkill` + `nohup`)
- [ ] `lsof -p $PID | grep cwd` 가 `~/Dev/SimpleClaw-prod` 인지 확인
- [ ] `git -C <cwd> log -1 --oneline` 이 릴리스 커밋인지 확인
- [ ] `tail -n 50 ~/.simpleclaw/bot.log` 에서 부팅 에러 없음 확인
- [ ] 본인 텔레그램 계정으로 `/ping` 또는 가벼운 질문 1회 → 정상 응답 수신

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

첫 실행 시 자동으로 생성되는 파일들 (운영 디렉터리 `~/.simpleclaw/` 기준 — BIZ-133):

```
~/.simpleclaw/
├── conversations.db    # 대화 히스토리 DB
├── daemon.db           # Cron 작업 DB
├── AGENT.md / USER.md / MEMORY.md / SOUL.md  # 라이브 페르소나
├── insights.jsonl      # 드리밍 인사이트 sidecar
├── HEARTBEAT.md        # 데몬 상태
├── _safety_backup/     # 사이클 직전 안전 백업
└── bot.log             # 봇 로그 (위 nohup 가이드 기준)
```

운영 트리(`~/Dev/SimpleClaw-prod`)와 개발 트리(`~/Dev/SimpleClaw`)는 같은 `~/.simpleclaw/` 를 가리키므로 데이터가 분리되지 않는다. 개발 봇과 운영 봇을 동시에 띄우려면 별도 토큰 + 별도 `config.yaml` 로 데이터 디렉터리도 분리해야 한다.
