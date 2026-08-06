# 설치 및 실행

## 요구사항

- Python 3.11+
- pip 또는 [uv](https://docs.astral.sh/uv/)
- 운영 배포 시 macOS `launchd` (현재 live 표준)

## 경로 정책

개발 코드, 운영에 배포된 코드, 실행 중 생성되는 상태를 분리한다. `scripts/run_bot.py`는 현재 working tree의 파일을 그대로 import하므로 운영 봇은 개발 트리에서 실행하지 않는다.

| 구분 | 표준 경로 | 용도 |
|---|---|---|
| 개발 트리 | `~/Dev/SimpleClaw` | feature 브랜치 작업, 테스트, PR |
| 배포/실행 트리 | `~/.simpleclaw` | `main` 코드, `.venv`, `config.yaml`, 실행 스크립트 |
| 에이전트 runtime state | `~/.simpleclaw-agent/default` | 페르소나, DB, workspace, recipes, Study Wiki, review/verification ledger, learning queue, drain state, 로그 |

`config.yaml.example`은 일부 mutable data 경로(`agent.db_path`, `agent.workspace_dir`, `recipes.dir`, `daemon.*`)를 `~/.simpleclaw` 아래에 두는 기본값을 제공한다. 현재 live 구성은 이 값들을 `~/.simpleclaw-agent/default`로 override해 배포 코드와 runtime state를 분리한다. 설치 후에는 아래 [경로 검증 체크리스트](#경로-검증-체크리스트)로 실제 `config.yaml`과 LaunchAgent가 어느 경로를 쓰는지 확인한다.

> `~/Dev/SimpleClaw-prod`는 과거 운영 worktree 예시다. 현재 표준 배포/실행 경로가 아니며 새 설치나 배포 명령에서 사용하지 않는다.

## 개발 환경 설치

```bash
git clone https://github.com/ingki3/simpleclaw.git ~/Dev/SimpleClaw
cd ~/Dev/SimpleClaw
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

설정 구조를 검토하거나 로컬 디버깅을 할 때만 개발 트리에 예제 설정을 복사한다.

```bash
cp config.yaml.example config.yaml
```

## 운영 배포 트리 설치

운영 코드는 개발 트리의 브랜치 전환과 독립된 `~/.simpleclaw` clone에서 실행한다.

```bash
git clone https://github.com/ingki3/simpleclaw.git ~/.simpleclaw
cd ~/.simpleclaw
git switch main
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.yaml.example config.yaml
```

`config.yaml`, `.env`, 암호화 볼트와 같은 git-ignored 운영 파일은 배포 갱신 전에 별도로 백업한다. 기존 `~/.simpleclaw` 데이터 디렉터리를 배포 트리로 전환하는 경우에는 [BIZ-304 운영 경로 마이그레이션 런북](runtime-migration-biz304.md)을 따른다.

## 환경 설정

### API 키

시크릿은 `config.yaml`에 평문으로 저장하지 않고 참조 문법을 사용한다.

- `env:NAME`: OS 환경변수
- `keyring:NAME`: macOS Keychain 또는 Linux Secret Service (권장)
- `file:NAME`: 암호화 볼트. 경로를 override하지 않으면 `~/.simpleclaw/secrets.enc`와 `~/.simpleclaw/master.key` 사용

환경변수를 사용하는 최소 예시는 다음과 같다.

```env
GOOGLE_API_KEY=your-gemini-api-key
ANTHROPIC_API_KEY=your-claude-api-key
OPENAI_API_KEY=your-openai-api-key
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
```

### runtime state 경로

현재 live와 같은 분리 구조를 사용하려면 `~/.simpleclaw/config.yaml`의 관련 키를 다음처럼 명시한다. 기능별 세부 설정은 `config.yaml.example`을 기준으로 유지한다.

```yaml
agent:
  db_path: "~/.simpleclaw-agent/default/conversations.db"
  workspace_dir: "~/.simpleclaw-agent/default/workspace"

recipes:
  dir: "~/.simpleclaw-agent/default/recipes"

daemon:
  pid_file: "~/.simpleclaw-agent/default/daemon.pid"
  status_file: "~/.simpleclaw-agent/default/HEARTBEAT.md"
  db_path: "~/.simpleclaw-agent/default/daemon.db"
  dreaming:
    insights_file: "~/.simpleclaw-agent/default/insights.jsonl"
    suggestions_file: "~/.simpleclaw-agent/default/suggestions.jsonl"
    blocklist_file: "~/.simpleclaw-agent/default/insight_blocklist.jsonl"
    runs_file: "~/.simpleclaw-agent/default/dreaming_runs.jsonl"
    safety_backup_dir: "~/.simpleclaw-agent/default/_safety_backup"

persona:
  local_dir: "~/.simpleclaw-agent/default"

study:
  wiki_dir: "~/.simpleclaw-agent/default/agent_wiki"

review:
  subagent_ledger:
    path: "~/.simpleclaw-agent/default/review_subagent_ledger.jsonl"
  verification_ledger:
    path: "~/.simpleclaw-agent/default/verification_evidence_ledger.jsonl"
```

### Runtime assets

runtime asset의 authoring SoT는 root `runtime_assets/**`입니다. 각 asset-local
`runtime-asset.yaml`이 identity, destination discovery, 설치 파일, SHA-256,
executable bit를 data로 선언합니다. generic resolver는 구체적인 업무명 없이
manifest를 검증한 뒤 전체 디렉터리를 atomic하게 교체합니다. wheel/sdist build는
같은 tree를 `simpleclaw/runtime_assets/**` package resource로 포함하므로 source
checkout과 installed distribution이 동일 resolver를 사용합니다.

filesystem source는 resolved asset root 내부의 regular file이어야 하며 symlink는
digest가 일치해도 거부합니다. 설치 중 staged write, directory swap, 기존 directory
cleanup이 실패하면 예외를 반환하기 전에 이전 destination을 복원하고 installer-owned
staged/backup directory를 제거합니다.

```bash
# Canonical interface
python scripts/install_runtime_asset.py --asset skill:naver-sports-skill
python scripts/install_runtime_asset.py --asset recipe:sports-live

# Compatibility wrappers (asset ref만 generic installer에 전달)
python scripts/install_naver_sports_skill.py
python scripts/install_sports_live_recipe.py --config ~/.simpleclaw/config.yaml
```

첫 번째 명령은 runtime global skill discovery 기본값 `~/.agents/skills`에
설치합니다. 두 번째 명령은 `config.yaml`의 `recipes.dir`를 읽으며, 설정 파일이나
키가 없으면 runtime 기본값 `~/.simpleclaw-agent/default/recipes`를 사용합니다.
일회성 override는 generic CLI의 `--destination`, compatibility recipe CLI의
`--recipes-dir`로 지정합니다. 각 명령의 출력에서 resolved destination,
source/package provenance, manifest SHA-256을 확인할 수 있습니다.

artifact를 검증하려면 wheel을 만든 뒤 manifest와 모든 declared resource가
포함됐는지 확인합니다.

```bash
python -m pip wheel . --no-deps --wheel-dir /tmp/simpleclaw-wheel
python -m zipfile -l /tmp/simpleclaw-wheel/simpleclaw-*.whl | grep runtime_assets
```

KBO production-artifact no-send 시나리오는 domain-neutral production harness에
fixture data와 installed definitions를 주입하는 아래 개발 CLI로 검증합니다.

```bash
python scripts/dev/validate_langgraph_v4_shadow.py --hermetic --mode primary --repeat 3
```

Live 반영은 asset backup 후 drain-aware restart와 위 검증을 거쳐야 하며,
installer 실행만으로 primary를 재활성화하지 않습니다.

browser handoff, proactive 제안, drain state, skill/recipe learning queue도 `config.yaml.example`의 `~/.simpleclaw-agent/default` 경로를 유지한다.

### 페르소나

라이브 페르소나는 개발/배포 트리 안의 `.agent/`가 아니라 `persona.local_dir`에 둔다. 현재 표준은 `~/.simpleclaw-agent/default`다.

```text
~/.simpleclaw-agent/default/
├── SOUL.md       # 정체성·말투
├── AGENT.md      # 역할·행동 지시
├── USER.md       # 사용자 정보
└── MEMORY.md     # 장기 기억
```

## 실행

### 로컬 개발/디버깅

```bash
cd ~/Dev/SimpleClaw
.venv/bin/python scripts/run_bot.py
```

`run_bot.py`는 Telegram 409 Conflict를 막기 위해 다른 `run_bot.py` 프로세스를 종료할 수 있다. 운영 봇이 실행 중인 머신에서는 별도 봇 토큰과 별도 데이터 경로가 준비되지 않은 한 개발 트리에서 실행하지 않는다.

### 운영 포그라운드 실행

```bash
cd ~/.simpleclaw
.venv/bin/python scripts/run_bot.py
```

### macOS LaunchAgent

현재 live LaunchAgent의 표준 label은 `com.simpleclaw.bot`이며 plist는 `~/Library/LaunchAgents/com.simpleclaw.bot.plist`에 둔다. 핵심 값은 다음과 같아야 한다.

| plist 키 | 기대 값 |
|---|---|
| `Label` | `com.simpleclaw.bot` |
| `WorkingDirectory` | `/Users/<user>/.simpleclaw` |
| `ProgramArguments[0]` | `/Users/<user>/.simpleclaw/.venv/bin/python` |
| `ProgramArguments[1]` | `scripts/run_bot.py` |
| `StandardOutPath`, `StandardErrorPath` | `/Users/<user>/.simpleclaw-agent/default/bot.log` |

설치된 값을 확인한다.

```bash
plutil -p ~/Library/LaunchAgents/com.simpleclaw.bot.plist
launchctl print "gui/$(id -u)/com.simpleclaw.bot"
tail -f ~/.simpleclaw-agent/default/bot.log
```

## 운영 트리 갱신과 재시작

릴리스 PR(`dev` → `main`)이 머지된 뒤 운영자 승인 하에 다음 순서로 갱신한다.

```bash
# 1) main을 fast-forward로 갱신한다. 로컬 tracked 변경이 있으면 실패하고 중단된다.
git -C ~/.simpleclaw fetch origin main
git -C ~/.simpleclaw pull --ff-only origin main

# 2) 의존성을 갱신한다.
~/.simpleclaw/.venv/bin/pip install -e ~/.simpleclaw

# 3) drain → restart → health smoke를 수행한다.
cd ~/.simpleclaw
.venv/bin/python scripts/deploy/drain_restart_simpleclaw.py \
  --issue-id BIZ-NNN \
  --reason "deploy BIZ-NNN"
```

재시작 스크립트는 새 intake를 drain하고 진행 중인 작업이 끝나기를 기다린 뒤 `launchctl kickstart`와 health smoke를 수행한다. live 재시작은 명시적인 운영자 승인 없이 실행하지 않는다.

갱신 후 다음을 확인한다.

```bash
git -C ~/.simpleclaw status --short --branch
git -C ~/.simpleclaw log -1 --oneline
launchctl print "gui/$(id -u)/com.simpleclaw.bot"
tail -n 50 ~/.simpleclaw-agent/default/bot.log
```

## 경로 검증 체크리스트

- [ ] 개발 트리는 `~/Dev/SimpleClaw`, 배포/실행 트리는 `~/.simpleclaw`이며 `~/Dev/SimpleClaw-prod`를 참조하지 않는다.
- [ ] `~/.simpleclaw/config.yaml`의 `persona.local_dir`가 `~/.simpleclaw-agent/default`다.
- [ ] `agent.db_path`, `agent.workspace_dir`, `recipes.dir`, `daemon.*`를 `config.yaml.example` 기본값 그대로 쓸지 live 분리 경로로 override할지 확인했다.
- [ ] Study Wiki와 review/verification ledger 경로가 `~/.simpleclaw-agent/default` 아래다.
- [ ] `~/Library/LaunchAgents/com.simpleclaw.bot.plist`의 `WorkingDirectory`와 Python 경로가 `~/.simpleclaw`을 가리킨다.
- [ ] LaunchAgent stdout/stderr가 `~/.simpleclaw-agent/default/bot.log`를 가리킨다.
- [ ] 실행 중 프로세스의 cwd와 배포 커밋을 확인했다.

```bash
PID=$(pgrep -f "scripts/run_bot.py" | head -1)
lsof -p "$PID" | awk '$4 == "cwd" {print "cwd:", $NF}'
git -C ~/.simpleclaw log -1 --oneline
```

## 테스트

```bash
# 단위 테스트
.venv/bin/python -m pytest tests/unit/

# 전체 테스트
.venv/bin/python -m pytest tests/

# lint
.venv/bin/python -m ruff check src/
```

## 첫 실행 후 디렉터리 구조

현재 live 분리 구성을 기준으로 한 예시다. opt-in 기능의 파일은 해당 기능이 활성화된 뒤 생성된다.

```text
~/.simpleclaw/                         # 배포/실행 트리
├── .git/
├── .venv/
├── config.yaml
├── secrets.enc / master.key           # file: backend 기본 경로
├── scripts/
└── src/

~/.simpleclaw-agent/default/           # mutable runtime state
├── SOUL.md / AGENT.md / USER.md / MEMORY.md
├── conversations.db / daemon.db
├── workspace/
├── recipes/
├── agent_wiki/                        # Agent Study Wiki (opt-in)
├── review_subagent_ledger.jsonl       # review ledger 사용 시
├── verification_evidence_ledger.jsonl
├── skill_suggestions.jsonl / recipe_suggestions.jsonl
├── _safety_backup/
└── bot.log
```

개발 봇과 운영 봇을 동시에 실행하려면 봇 토큰뿐 아니라 `config.yaml`의 모든 mutable state 경로도 서로 다른 디렉터리로 분리한다.
