# SimpleClaw

**확장 가능한 개인 비서 AI 에이전트** (Python)

SimpleClaw는 사용자의 일상 업무를 자율적으로 처리하는 AI 에이전트입니다. 역할 기반 LLM 라우팅으로 여러 프로바이더(Claude/Gemini/OpenAI)를 유연하게 조합하고, 스킬·레시피·MCP로 기능을 확장하며, 텔레그램으로 언제 어디서든 소통할 수 있습니다. 모든 설정은 `config.yaml` 하나로 관리됩니다.

## 주요 기능

| 기능 | 설명 |
|-----|------|
| **역할 기반 LLM 라우팅** | `llm.routes`가 역할(default / turn_analysis / multimodal)별 primary·retry 백엔드를 선택합니다. 각 백엔드는 wire `transport`(anthropic / openai_chat / gemini / vertex_gemini / cli)와 엔드포인트 `profile`을 명시적으로 지정하므로, 모델 교체·A/B가 코드 수정 없이 설정만으로 가능합니다. |
| **LLM Turn 분석 (TurnAnalysis)** | 일반 대화 앞단에서 LLM structured JSON 호출 1회로 follow-up 맥락 복원, clarify 필요성, 도메인/의도, 실행 경로(standard/current/complex)를 판단합니다. 분석 실패 시 결정적(keyword) 라우터로 안전하게 fallback합니다. |
| **Native Function Calling 도구 루프** | `AgentOrchestrator`가 페르소나·자산 선택·컨텍스트를 준비하고, `ToolLoopRunner`가 LLM 호출 → 도구 실행 → observation 반영을 반복 제어합니다(최대 반복 횟수 제한). |
| **실행 결과 Ledger 복구** | `ActionResultLedger`가 도구 실행 결과(성공/실패/side-effect)를 step 단위로 구조화 기록합니다. LLM이 도구 실행 후 빈 최종 응답을 반환해도, 이미 완료된 side-effect(예: 일정 생성)를 잃지 않고 결정적으로 보고합니다. |
| **실시간 근거 가드 (complex fact workflow)** | 최신성이 중요한 복잡한 사실 질문은 evidence slot 기반 워크플로우로 처리합니다. 수집한 근거의 신선도(확정/진행 중/사전 정보/낡음)와 출처 등급을 검증하고, 근거가 비면 최종 답변의 단정 표현을 보수적으로 차단합니다. |
| **페르소나 시스템** | `SOUL.md`(정체성·말투) → `AGENT.md`(역할 지시) → `USER.md`(사용자 정보) → `MEMORY.md`(장기 기억) 순으로 시스템 프롬프트를 조립합니다. 매 메시지마다 디스크에서 다시 읽어 재시작 없이 반영됩니다(hot-reload). |
| **스킬 실행** | `SKILL.md`로 정의된 도구(Python/Bash 스크립트)를 자동으로 발견하고, CommandGuard·시크릿 스트리핑·프로세스 격리를 거쳐 실행합니다. 매 turn 전체 목록 대신 selector가 관련 스킬 top-k만 프롬프트에 노출해 prompt bloat를 줄입니다. |
| **레시피 & 슬래시 커맨드** | YAML(`recipe.yaml`)로 정의된 워크플로우를 텔레그램 `/recipe-name`으로 즉시 실행합니다. 레시피는 매 메시지마다 디스크에서 재탐색되어 재시작이 필요 없습니다. |
| **MCP 통합** *(opt-in)* | stdio 방식 MCP 서버를 연결해 외부 도구를 `mcp_call`로 호출합니다. 서버별 `scope: runtime`(일반 tool loop 노출) / `scope: operator`(운영자 전용, 기본값)로 노출 범위를 통제합니다. 기본 비활성. |
| **Browser Handoff** *(opt-in)* | Cloudflare/403 등으로 headless fetch가 막힌 URL을 로컬 Chrome + 확장 프로그램 + Native Messaging으로 회수합니다. 민감 도메인 차단 정책 내장, cron/background에서는 Chrome을 열지 않습니다. 기본 비활성 — 설계: `docs/browser_handoff.md`. |
| **대화 기억** | 모든 대화를 SQLite(현재 live override: `~/.simpleclaw-agent/default/conversations.db`)에 저장하고 최근 히스토리를 LLM 호출 시 자동 주입합니다. |
| **시맨틱 메모리 (RAG)** *(opt-in)* | `sentence-transformers` 다국어 임베딩으로 과거 대화를 의미 기반 회상합니다. tool loop에서는 내장 `search_memory` 도구로 온디맨드 검색. 첫 활성화 시 임베딩 모델(~500MB)을 다운로드합니다. 기본 비활성. |
| **LLM 드리밍** | 심야에 대화를 분석해 기억 요약을 `MEMORY.md`, 사용자 인사이트를 `USER.md`에 기록합니다. 기본은 dry-run — 인사이트가 승인 큐에 쌓이고 운영자가 검수 후 반영됩니다(강한 시그널만 auto-promote). 실행 전 라이브 파일 전체를 안전 백업합니다. |
| **Agent Study Wiki** *(opt-in)* | 사용자 기억과 분리된 "외부 세계 배경지식" Markdown 위키. 매일 정해진 시각에 관심 주제를 수집·정리하고, 질문 응답 시 신선도 기준으로 맥락 주입합니다. 출처 필수 등 사실성 가드 내장. 기본 비활성 — 설계: `docs/agent-study-wiki.md`. |
| **Proactive 제안** *(opt-in)* | 대화·일정·메일 맥락에서 선제 제안(예: cron 후보)을 추출해 저장하고, 사용자가 텔레그램에서 승인해야만 실제 등록됩니다(fail-closed). 기본 비활성. |
| **스킬/레시피 학습** *(opt-in)* | 성공한 복잡 tool trace를 스킬/레시피 후보로 pending 큐에 저장합니다. 자동 설치는 없으며, 운영자가 검토(show/diff) → accept → materialize(confirm 필수)를 거쳐야만 실제 runtime 자산이 됩니다. 기본 비활성. |
| **Review/검증 Ledger** | subagent review gate 결과와 issue 단위 검증 근거(CI/배포/health smoke 등)를 JSONL ledger로 구조화 기록합니다. required gate 완료 전 merge/done 판정을 막는 근거 데이터를 제공하며, 조작은 운영자 전용 도구로만 가능합니다. |
| **백그라운드 데몬** | Heartbeat 모니터링, Cron 스케줄링(재시도/circuit-break, NO_NOTIFY 지원), 드리밍 자동 트리거. Cron 메시지는 대화 히스토리와 격리 처리됩니다. |
| **Drain/재시작** | 배포 시 `scripts/deploy/drain_restart_simpleclaw.py`가 drain 상태를 걸어 새 유입을 거절하고 진행 중 turn을 완료시킨 뒤 재시작합니다. 운영자 context에서는 `restart_runtime` 도구(confirm 필수)로 채팅에서 재시작할 수 있습니다. |
| **텔레그램 봇** | 화이트리스트 기반 접근 제어(fail-closed). 이미지·문서(PDF/DOCX/XLSX/PPTX 등, 최대 20MB) 첨부를 멀티모달 route로 분석하고, clarify 선택지는 인라인 키보드로 렌더합니다. 점진 스트리밍 응답은 opt-in(기본 비활성, cron 알림은 항상 완성 후 발송). |
| **Admin API & UI** | 봇 데몬이 `127.0.0.1:8082`에 Bearer 토큰 인증 REST 서버를 띄웁니다(토큰 미설정 시 부팅 실패 — silent insecure 방지). `web/admin`(:8088), `web/admin-2`(:8089) UI가 이를 사용합니다. |
| **Webhook 리스너** | Zapier, n8n 등 외부 이벤트 수신용 REST 엔드포인트. 토큰 인증, 페이로드 크기 제한, rate limit, 동시성 cap 내장. |
| **음성 (STT/TTS)** | OpenAI Whisper/TTS API를 통한 음성 입출력. |
| **보안 실행 환경** | CommandGuard 위험 명령 차단, subprocess 시크릿 스트리핑, 프로세스 그룹 격리(`os.setsid`), 시크릿 매니저(keyring/환경변수/암호화 파일) 참조 문법. |
| **로깅** | 구조화 JSONL 로그, 메트릭 수집, trace context, 대시보드 모듈. |

## 동작 방식

사용자 메시지 1건이 처리되는 파이프라인:

```
사용자 메시지 (Telegram / Webhook)
  │
  ├─ /recipe-name, /goal, /cron 등 커맨드? ── YES → 커맨드/레시피 실행 → 응답
  │
  ▼
[1] TurnAnalysis (LLM structured JSON, llm.routes.turn_analysis)
      follow-up 맥락 복원 · clarify 필요성 · 도메인/의도 · 실행 경로 결정
      실패 시 → 결정적(keyword) 라우터로 fallback
  │
  ▼
[2] AgentOrchestrator
      페르소나 조립(SOUL→AGENT→USER→MEMORY, hot-reload)
      + 자산 선택(스킬/레시피 top-k) + 대화 히스토리 주입
  │
  ▼
[3] ToolLoopRunner — Native Function Calling 루프 (최대 N회)
      LLM이 tool_calls 반환 → 도구 실행 → observation 추가 → 재호출
      도구 실행마다: CommandGuard → 시크릿 스트리핑 → 프로세스 격리 → 실행
      각 step을 ActionResultLedger에 구조화 기록
      complex 경로는 evidence slot 검증 + claim guard를 추가로 거침
  │
  ▼
[4] 최종 응답
      LLM이 빈 최종 응답을 반환하면 ledger에서 side-effect 성공 근거를
      찾아 결정적 fallback 텍스트 생성 → DB 저장 → 사용자 응답 전달
```

## 프로젝트 구조

```
src/simpleclaw/
  agent/            # AgentOrchestrator, ToolLoopRunner, 내장 도구, TurnAnalysis,
                    #   fact workflow(근거 검증), goal loop, 커맨드 디스패치
  llm/              # LLMRouter, routes/providers, transports(wire)·profiles(endpoint) 레지스트리
  persona/          # 페르소나 파서, 어셈블러, 리졸버 (SOUL/AGENT/USER/MEMORY)
  skills/           # 스킬 디스커버리, 실행기, MCP 클라이언트
  recipes/          # 레시피 로더, 실행기
  memory/           # 대화 저장소(SQLite), 임베딩/RAG, 드리밍 파이프라인
  study/            # Agent Study Wiki (주제 레지스트리, 수집기, 회상)
  proactive/        # Proactive opportunity 큐 + TPO 정책 엔진
  review/           # subagent review ledger, verification evidence ledger
  browser_handoff/  # Chrome native messaging 기반 본문 회수
  daemon/           # 데몬, 하트비트, 크론 스케줄러, drain, 대기 상태
  security/         # CommandGuard, 시크릿 매니저, 환경변수 필터, 프로세스 격리
  channels/         # Telegram 봇, Webhook 서버, Admin API
  agents/           # 서브 에이전트 풀, 스포너, 워크스페이스
  voice/            # STT/TTS
  logging/          # 구조화 로거, 메트릭, 대시보드
  config.py         # 설정 로더 (config.yaml → 각 서브시스템)
prompts/            # 시스템/드리밍/스터디 프롬프트 SoT (코드에 하드코딩하지 않음)
web/admin, web/admin-2   # Admin UI
scripts/            # run_bot.py 등 thin wrapper (비즈니스 로직 없음)
tests/              # unit / integration / 시나리오 / E2E
docs/               # 기능별 상세 문서 (docs/index.md 참조)
```

코드 구조·의존 관계는 `graphify-out/`(지식 그래프 분석 결과)을 함께 참고하세요.

## 시작하기

### 사전 요구 사항

- Python 3.11 이상
- pip 또는 [uv](https://docs.astral.sh/uv/)

### 설치

```bash
git clone https://github.com/ingki3/SimpleClaw.git
cd SimpleClaw
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # 운영만 필요하면 pip install -e .
```

### 설정

1. `config.yaml.example`을 복사해 `config.yaml`을 만듭니다:

```bash
cp config.yaml.example config.yaml
```

2. 시크릿을 등록합니다. `config.yaml`의 모든 시크릿 필드는 참조 문법을 지원합니다:

   - `"env:NAME"` — OS 환경변수
   - `"keyring:NAME"` — macOS Keychain / Linux Secret Service (권장)
   - `"file:NAME"` — `~/.simpleclaw/secrets.enc` 암호화 볼트 (헤드리스 환경, 마스터 키는 `~/.simpleclaw/master.key` 또는 `SIMPLECLAW_MASTER_KEY` 환경변수)

   ```bash
   .venv/bin/python -c "
   from simpleclaw.security.secrets import SecretsManager
   m = SecretsManager()
   m.store('keyring', 'claude_api_key', 'sk-ant-...')
   m.store('keyring', 'telegram_bot_token', '123:ABC...')
   "
   ```

   기존 `.env`/평문 config에서 이전할 때는 `scripts/migrate_secrets.py --backend keyring --rewrite-config`.

3. 페르소나 파일을 만듭니다. 라이브 페르소나는 저장소 밖 `~/.simpleclaw-agent/default/`에 둡니다 (`persona.local_dir`):

   - `SOUL.md` — 정체성·말투 (없으면 warning 후 skip)
   - `AGENT.md` — 역할·행동 지시
   - `USER.md` — 사용자 정보
   - `MEMORY.md` — 장기 기억 (드리밍이 관리)

### LLM 라우팅 설정

백엔드 이름은 벤더가 아니라 **역할**입니다. `routes`가 역할별 primary/retry를 고르고, 각 백엔드가 transport(전송 방식)와 profile(엔드포인트 규약)을 명시합니다. 변경은 재시작 후 반영됩니다.

```yaml
llm:
  routes:
    default: {primary: chat_primary, retry: chat_fallback}
    turn_analysis: {primary: analysis_fast, retry: analysis_safe}
    multimodal: {primary: vision_native}
  providers:
    chat_primary:
      type: "api"
      model: "claude-sonnet-4-20250514"
      transport: "anthropic"
      profile: "anthropic"
      api_key: "keyring:claude_api_key"
    chat_fallback:
      type: "api"
      model: "gpt-4o"
      transport: "openai_chat"
      profile: "openai"
      api_key: "keyring:openai_api_key"
    analysis_fast:
      type: "api"
      model: "gemini-3.5-flash"
      transport: "gemini"
      profile: "gemini"
      api_key: "env:GOOGLE_API_KEY"
```

Gemini의 OpenAI-호환 엔드포인트(A/B 전용)와 Vertex AI Gemini(GCP IAM 인증)는 `config.yaml.example`에 주석으로 준비되어 있으며 필요 시 opt-in합니다.

### 텔레그램 봇 실행 (로컬 개발)

1. [@BotFather](https://t.me/BotFather)에서 봇 생성, [@userinfobot](https://t.me/userinfobot)에서 본인 User ID 확인
2. 토큰을 시크릿 매니저에 등록하고 `config.yaml`에 참조와 화이트리스트 추가:

```yaml
telegram:
  bot_token: "keyring:telegram_bot_token"
  whitelist:
    user_ids: [본인-User-ID]
```

3. 개발 트리에서 실행 (로컬 개발/디버깅 용도):

```bash
.venv/bin/python scripts/run_bot.py
```

> **주의** — `run_bot.py`는 시작 시 다른 `run_bot.py` 프로세스를 모두 종료합니다(Telegram 409 방지). 운영 봇이 도는 머신에서 개발 트리로 실행하면 운영 봇이 죽고 그 자리에 개발 코드가 올라갑니다. **운영 봇은 배포 트리(`~/.simpleclaw`)에서 실행합니다** — 아래 §운영 경로 참조.

## 운영 경로 (어디에 무엇이 있는가)

| 경로 | 내용 |
|---|---|
| `~/Dev/SimpleClaw` | **개발 트리** — feature 브랜치 작업·테스트·PR. 봇 실행은 로컬 개발/디버깅 용도로만 |
| `~/.simpleclaw/` | **배포/실행·설정 트리** — 운영 봇이 import하는 배포 코드와 `.venv`, `config.yaml`, 기본 시크릿 볼트(`secrets.enc`·`master.key`) 등 git-ignored 운영 설정 |
| `~/.simpleclaw-agent/default/` | **mutable runtime state** — 대화·데몬 DB, workspace, recipes, 드리밍 sidecar·안전 백업, 라이브 페르소나(SOUL/AGENT/USER/MEMORY), Study Wiki, review/verification ledger, 스킬·레시피 학습 후보 큐, drain 상태, browser-handoff 저장소, 로그 |

핵심 규약은 **개발 트리·운영 실행·mutable runtime state의 분리**입니다. `run_bot.py`는 체크아웃된 트리의 코드를 그대로 import하므로, 운영 봇은 배포 트리(`~/.simpleclaw`)에서만 실행하고 개발 트리에서는 코드 작업만 합니다 — 미머지 feature 브랜치 코드가 운영에 올라가는 사고를 막기 위함입니다. 배포 트리를 갱신·정리하기 전에는 git-ignored 설정과 시크릿 볼트를 별도로 백업하세요. 대화 DB 등 mutable runtime state는 live override에 따라 `~/.simpleclaw-agent/default`에 유지됩니다. 자세한 설치·실행·경로 검증 절차는 [docs/installation.md](docs/installation.md)를 참고하세요.

## 도구 · 스킬 · 레시피 · MCP 경계

에이전트가 쓸 수 있는 실행 수단은 네 층으로 나뉩니다:

- **내장 도구 (native tools)** — 코드에 정의된 함수 호출 도구. `cli`, `web_fetch`, `web_search`, `file_read`/`file_write`/`file_manage`, `skill_docs`, `search_memory`, `clarify`, `cron`, `browser_handoff` 등. 각 도구는 scope(runtime/operator)와 risk 등급을 가지며, `restart_runtime`·`skill_learning`·`recipe_learning`·`review_subagent_ledger`·`verification_evidence` 같은 운영자 도구는 operator context에서만 노출되고 위험 동작은 confirm을 요구합니다.
- **스킬** — `SKILL.md`로 정의된 외부 스크립트. 로컬 `.agent/skills/`가 전역 `~/.agents/skills/`보다 우선하며, `execute_skill` 도구가 CommandGuard·시크릿 스트리핑·격리를 거쳐 실행합니다.
- **레시피** — 현재 live override인 `~/.simpleclaw-agent/default/recipes/<name>/recipe.yaml`의 `instructions` 프롬프트를 도구 루프로 실행하는 다단계 워크플로우. 텔레그램 `/recipe-name` 또는 cron으로 트리거됩니다.
- **MCP 서버** *(opt-in)* — stdio MCP 서버의 도구를 `mcp_call`로 호출. 서버별 scope로 일반/운영자 노출을 구분하며 기본 비활성입니다.

## Admin API

봇 데몬은 Admin UI 백엔드용 REST 서버를 `127.0.0.1:8082`에 바인딩하고 모든 호출에 Bearer 토큰을 강제합니다. **토큰 미설정 시 봇이 부팅하지 않습니다.**

```bash
# 토큰 발급 + Admin UI 환경 동기화 (idempotent, 한 번만)
.venv/bin/python scripts/setup_admin_api.py

# 호출 예시
TOKEN=$(.venv/bin/python -c "from simpleclaw.security.secrets import SecretsManager; print(SecretsManager().resolve('keyring:admin_api_token'))")
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8082/admin/v1/health
```

개발/CI에서 불필요하면 `admin_api.enabled: false`. Admin UI dev 서버 origin은 `admin_api.cors_origins`에 추가합니다(기본: `:8088`, `:8089`).

## 테스트

```bash
# 단위 테스트 (빠름, CI 필수 — API 키 불필요)
.venv/bin/python -m pytest tests/unit/

# 전체 테스트
.venv/bin/python -m pytest tests/

# 린터
.venv/bin/python -m ruff check src/
```

| 계층 | 경로 | API 키 필요 |
|------|------|-------------|
| 단위 | `tests/unit/` | 아니오 |
| 통합 | `tests/integration/` | 일부 (`offline`/`live`/`external` 마커로 구분) |
| 시나리오 | `tests/test_*_scenarios.py` | 예 |
| E2E | `tests/test_e2e_*.py` | 예 |

## 설계 원칙

1. **Python 전용** — 코어 런타임에 다른 언어 의존성 없음
2. **경량 의존성** — asyncio + APScheduler + SQLite, Docker/Celery/Redis 불필요
3. **설정 기반** — 모든 동작을 `config.yaml`로 제어, 프롬프트는 `prompts/`에서 SoT 관리
4. **다층 보안** — 위험 명령 감지, 시크릿 스트리핑·참조 문법, 프로세스 격리, 화이트리스트, 운영자 confirm 게이트
5. **승인 우선 자동화** — 드리밍 인사이트·proactive 제안·스킬/레시피 학습 모두 기본은 후보 큐 + 운영자 승인, 자동 반영은 opt-in
6. **개발/운영 실행 분리** — 운영 봇은 배포 트리(`~/.simpleclaw`)에서만 실행해 미머지 개발 코드가 운영에 올라가지 않도록 함
7. **Lazy Loading** — 페르소나·스킬·레시피를 매 메시지 디스크에서 재로드하여 무중단 변경
8. **복구 가능한 실행** — 실행 결과 ledger·검증 ledger·안전 백업으로 실패·빈 응답·사고에서 결정적 복구

## 개발 가이드

```
feature/xxx  ──(PR, Squash)──>  dev  ──(PR, Merge commit)──>  main
```

`main`과 `dev`는 직접 push 불가, PR을 통해서만 merge합니다. `main` 머지 시 calver 태그와 GitHub Release가 자동 생성됩니다. 코딩 에이전트 규약은 [AGENTS.md](AGENTS.md), 기능별 상세 문서는 [docs/index.md](docs/index.md)를 참고하세요.

## 라이선스

MIT
