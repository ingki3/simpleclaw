# Product Requirements Document (PRD): Personal Assistant Agent

## 1. 개요 (Product Overview)
본 제품은 사용자의 개인 비서 역할을 수행하는 확장 가능하고 자율적인 AI Agent입니다. 사용자는 설정 파일을 통해 Agent의 성격을 규정하고 능력을 확장할 수 있으며, 다양한 LLM을 유연하게 교체하며 사용할 수 있습니다. 기존의 코드 레벨의 보조를 넘어 일상적인 업무, 자동화, 스케줄링 등을 책임지는 지능형 비서를 목표로 합니다.

## 2. 목표 및 핵심 가치 (Goals & Core Values)
- **개인화 (Personalization):** 사용자의 요구와 작업 스타일에 맞춰 성격, 기억, 루틴을 커스터마이징.
- **확장성 (Extensibility):** 자체 Skill, 서버 기반의 MCP(Model Context Protocol), 그리고 외부 Agent/도구를 자유롭게 연결하여 능력 확장.
- **자율성 및 자동화 (Autonomy & Automation):** 사용자의 개입을 최소화하는 Cron Job 기반의 자동화 작업 및 레시피(Recipe) 기반의 워크플로우 지원.
- **유연성 (Flexibility):** 단일 LLM에 종속되지 않고 Gemini, ChatGPT, Claude 등 다양한 모델과 CLI 인터페이스 연동.

## 3. 핵심 기능 (Core Features)

### 3.1. 에이전트 페르소나 및 메모리 시스템 (Persona & Memory)
**참고:** OpenClaw / Hermes Agent
- `AGENT.md`: Agent의 주체적 성격, 기본 역할, 그리고 응답 스타일 및 톤앤매너 지정.
- `USER.md`: 소유자/사용자에 대한 정보와 상호작용 규칙 및 선호도 정의.
- **대화 원시 로그 (Structured Message History):** 실시간 대화 내역 및 스킬 실행 결과는 휘발성 인메모리가 아닌, 타임스탬프, 역할(Role), 토큰량 등 메타데이터를 포함하여 반정형 파일(`.jsonl`)이나 혹은 로컬 `SQLite` DB에 원형 그대로 구조화하여 저장. (프롬프트에는 이 DB에서 최근 N개의 대화만 슬라이딩 윈도우로 쿼리하여 주입).
- `MEMORY.md` (Core Memory): 드리밍 프로세스가 LLM을 사용하여 DB의 대화 원시 로그를 분석하고 도출해낸 **"핵심 요약본(완료된 작업, 장기간 유지할 정보)"**만을 텍스트로 깔끔하게 정리하여 보존.
- **시맨틱 메모리 (RAG):** 단순 텍스트 파일을 넘어 벡터 DB 중심의 검색 증강 생성을 활용해 방대한 양의 과거 이력을 자동 색인하고 문맥에 맞춰 쿼리.
- **LLM 기반 드리밍(Dreaming) 및 기억 통합:** 1일 1회 실행을 원칙으로 하며 "마지막 입력 시점에서 2시간 초과 대기" 및 "지정된 심야 시간(예: 03:00) 통과" 조건을 동시 만족 시 동작. **LLM을 사용하여** 대화를 분석하고 두 가지를 추출: (1) 기억 요약(이벤트, 결정사항) → `MEMORY.md`, (2) 사용자 인사이트(선호도, 관심사) → `USER.md`. `AGENT.md`는 드리밍으로 수정되지 않음. 드리밍에 사용할 모델은 `daemon.dreaming.model`로 설정 가능. 실행 전 반드시 `.bak` 백업 파일을 생성해 오염(Hallucination) 시 빠른 수동 롤백을 보장.
- **Lazy Loading / Hot Reload:** 페르소나 파일(AGENT.md, USER.md, MEMORY.md), 스킬, 레시피를 매 메시지 처리 시 디스크에서 다시 읽어 반영. 파일 변경 시 에이전트 재시작이 필요 없음.
- `HEARTBEAT.md`: Agent 자율 틱(tick) 기반 백그라운드 상태 모니터링 시스템. 토큰 및 CPU 소모 최적화를 위해 기본 주기는 5분(Config 조절 가능)으로 고정하며, DB 쓰기(Flush)는 기억 데이터의 변동 가능성(Dirty state)이 감지될 시에만 실행.

### 3.2. 도구 확장 시스템: Skill, MCP, Agent 호출
**참고:** OpenClaw / Paperclip
- **Skill 관리 및 구동 엔진:** 
  - **구동 메커니즘**: 에이전트는 기본적으로 지정된 `SKILL.md` 파일을 파싱해 스킬의 목적과 가이드를 이해하고, 연결된 타겟 스크립트를 호출해 동작을 수행하는 표준적인 스킬 실행 로직을 지원해야 함.
  - **스킬 디렉토리 및 관리 방식 (Global vs Local)**: 
    1. **전역(Global) 스킬**: 운영체제 전역에 설치되어 다른 프로파일서도 공용으로 쓰는 스킬 (예: `~/.agents/skills/`).
    2. **에이전트 전용(Local) 스킬**: 현재 구동 중인 개인 에이전트 환경만을 위한 전용 스킬. 워크스페이스 내부의 격리된 경로(예: `.agent/skills/`)에 별도로 설치 및 관리. (동일한 스킬 이름일 경우 전용 스킬을 우선 로드하여 Override 지원).
  - **패키지 의존성 최적화**: 각 스킬에 대한 독립적인 가상환경(venv) 구축을 최소화하고, 시스템 복잡성을 낮추기 위해 `uv run` 등의 스크립트별 인라인 의존성 관리를 권장.
- **MCP (Model Context Protocol):** 외부 서비스 및 데이터베이스와 상호작용하기 위한 표준 프로토콜 지원.
- **Agent 호출:** 특정 도메인에 특화된 다른 에이전트(예: 코딩 전용 에이전트)에게 작업을 위임하고 결과를 반환받음.

### 3.3. 서브 에이전트 (Sub-Agent) 분리 (ACP 활용)
**참고:** OpenClaw / Goose
- **ACP 기반 단발성 협업 (서브프로세스 런타임):** 메인 프로세스가 복잡한 태스크를 분리해 서브 에이전트를 `asyncio.create_subprocess_exec` 형태의 비동기 서브프로세스로 동적 스폰(Spawn). 통신은 별도의 통신 인프라 없이 **표준 JSON-over-Stdout**을 채택하며 단발성(Short-lived)으로 임무 완수 후 즉각 소멸.
- **제어 및 보안:** 무한 스폰으로 인한 시스템 자원 고갈을 막기 위해 띄울 수 있는 서브 에이전트 최대치는 3개(Config 설정)로 강제 하드 리밋(Limit) 설정. 서브 에이전트는 기동 시 `{ "allowed_paths": [".agent/workspace/"], "network": true }` 형태의 명시적 권한(Scope)을 주입받아 통제됨.

### 3.4. 워크플로우 레시피 (Recipes) 지원
**참고:** Goose
- **전용 저장소 및 YAML 정의:** 반복적이고 복잡한 다단계 작업은 일반 Skill과 격리된 전용 디렉토리(`.agent/recipes/`)에 `recipe.yaml` 형태로 저장. v2 포맷의 `instructions` 필드에 프롬프트를 작성.
- **슬래시 커맨드 실행:** 텔레그램에서 `/recipe-name`을 입력하면 해당 레시피를 즉시 실행. 레시피는 매 메시지마다 디스크에서 재탐색되므로 추가/수정 시 재시작 불필요.
- **재사용성:** 프롬프트, 필요 변수(Parameters), 실행할 플러그인(Extensions/Builtin) 등을 사전 정의하여 명령어 하나로 일련의 워크플로우를 자동 실행.

### 3.5. 명령 실행 보안 및 멀티턴 도구 루프 (Security & Multi-Turn Tool Loop)
**참고:** Hermes Agent (`tools/approval.py`, `tools/environments/local.py`, `run_agent.py`)

#### 3.5.1. 위험 명령 감지 (Dangerous Command Guard)
- 모든 subprocess 명령 실행 전, 35개 이상의 정규식 패턴으로 위험 명령을 감지하여 차단.
- 대상 카테고리: 파일 삭제(`rm -rf`), Git 파괴(`push --force`, `reset --hard`), DB 파괴(`DROP TABLE`), 권한 변경(`chmod 777`), pipe-to-shell(`curl|bash`), 시스템 명령(`reboot`, `shutdown`) 등.
- ANSI 이스케이프 제거, 유니코드 NFKC 정규화, null 바이트 제거로 우회 방지.
- `config.yaml`의 `security.command_guard.allowlist`에 패턴 키를 등록하여 특정 명령을 예외 처리.
- 적용 범위: Agent 명령 실행(`agent.py`), Recipe 단계 실행(`recipes/executor.py`).

#### 3.5.2. Subprocess 시크릿 스트리핑 (Environment Secret Filtering)
- subprocess 실행 시 `os.environ`을 복사한 후 민감 키를 제거하여 전달.
- 차단 패턴: `*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `TELEGRAM_*`, `OPENAI_*`, `ANTHROPIC_*`, `GOOGLE_*`, `AWS_*`, `WEBHOOK_*`, `GH_TOKEN`, `GITHUB_*`.
- `config.yaml`의 `security.env_passthrough`에 등록된 키는 차단에서 제외.
- 적용 범위: Agent 명령 실행, 스킬 스크립트 실행(`skills/executor.py`), Recipe 명령 실행.

#### 3.5.3. 프로세스 그룹 격리 (Process Group Isolation)
- 모든 subprocess를 `os.setsid`로 독립 프로세스 그룹에서 실행.
- timeout 발생 시 `os.killpg`로 프로세스 그룹 전체를 종료 (SIGTERM → 대기 → SIGKILL).
- 좀비 프로세스 및 자식 프로세스 릭 방지.

#### 3.5.4. ReAct (Reasoning + Acting) 추론 루프
- 기존 멀티턴 스킬 라우터를 **ReAct 패턴**으로 대체.
- LLM이 명시적으로 **Thought**(추론), **Action**(도구 호출), **Observation**(결과 관찰) 사이클을 반복.
- 결과가 불완전하거나 부정확하면 LLM이 스스로 다른 접근으로 재시도 (자기 보정).
- 작업이 완료되면 **Answer**를 생성하여 최종 응답.
- `config.yaml`의 `agent.max_tool_iterations`로 최대 반복 횟수 제한 (기본: 5).
- 단일 도구만 필요한 경우 루프가 1회만 실행되어 기존 동작과 완전히 호환.

#### 3.5.5. 스마트 Python 경로 감지
- `_fix_python_path()` 함수가 스킬 스크립트 근처의 venv를 자동 탐지하여 올바른 Python 인터프리터로 실행.
- 스킬별 독립 가상환경 지원을 별도 설정 없이 자동화.

### 3.6. 스케줄링, 이벤트 트리거 및 비동기 워크플로우
**참고:** Hermes Agent / Paperclip
- **Cron Job 관리:** 정해진 시간에 특정 프롬프트나 레시피를 실행하는 Cron Job 생성, 조회, 수정, 삭제 기능.
- **Cron 격리 실행:** Cron 메시지는 `process_cron_message()`로 처리되며, 대화 히스토리를 포함하지 않고 공유 DB에 저장하지 않아 일반 대화와 완전히 격리.
- **NO_NOTIFY 알림 제어:** Cron job 결과에 알릴 내용이 없으면 LLM이 `[NO_NOTIFY]` 토큰으로 응답하여 텔레그램 알림을 생략.
- **이벤트 트리거 통합 (Webhook 전용):** 시스템 단편화를 막기 위해 이메일/캘린더 서비스 직접 연동 코어 탑재를 배제. 단 하나의 범용적인 **REST Webhook 수신 엔드포인트(FastAPI 등)**만 오픈하고, 타 서비스 통신(Zapier, n8n 등)이 이를 트리거하는 간결망으로 파이프라인 집중.
- **비동기 대기 (Asynchronous Wait States):** 외부 처리 대기나 사용자 응답이 필요한 작업 진행 시 에이전트 작동을 일시 정지(Pause)해두고 다른 업무를 수행하다가, 콜백이 오거나 조건이 만족되면 멈춘 지점부터 작업을 재개해 효율성을 강화.
- **백그라운드 실행:** 사용자가 오프라인인 상태에서도 뉴스를 스크랩하거나, 모니터링 경고를 확인하여 보고서를 남기는 등의 백그라운드 태스크 수행.

### 3.6. 메신저 채널, 오디오 모달리티 및 다중 LLM/CLI 통합 지원
**참고:** Paperclip / Hermes Agent
- **Telegram 연결:** 텔레그램(Telegram) 메신저와의 봇 연동을 지원하여 사용자가 외부에서도 스마트폰 등을 통해 실시간으로 업무 지시를 내리고 결과를 보고받을 수 있는 양방향 통신 채널 제공.
- **음성/오디오 인터페이스 (STT/TTS):** 메신저나 음성 파일을 통해 사용자의 오디오 명령을 텍스트로 변환하여 이해(STT)하고, 에이전트가 분석한 브리핑 등 결과물을 자연스러운 음성(TTS)으로 피드백해주는 확장 기능 지원.
- **제한적 다중 LLM 라우팅:** 시스템 내 불확실성을 유발하는 다이나믹 오토-라우터(자동 모델 분할) 대신, 환경 설정(`config.yaml`)에 사용자가 배포한 지정 규칙(예: 메인은 Claude, 코딩 서브는 ChatGPT)을 따르는 **설정 기반 수동 라우팅을 원칙**으로 시스템 예측성과 투명성 유지.
- **CLI 연동 및 실행 주체 구분:** 에이전트 코어가 외부 서드파티 CLI(`claude cli`, `goose` 등)를 자신이 사용할 수 있는 독립된 도구 스크립트로 간주하여, **OS 서브프로세스로 해당 CLI를 명령 호출(Exec) 후 응답 텍스트를 파싱**하는 래핑 기법 채택.

## 4. 기술적 구현 사양 및 룰 (Technical Specifications)

### 4.1. Workspace 및 페르소나/메모리 저장 룰
- **메인 에이전트(Main Agent) 디렉토리**: 기본적으로 `~/.agents/main/` 또는 실행 프로젝트 루트의 `.agent/` 폴더를 워크스페이스로 사용하여 구동.
- **스킬 워크스페이스**: `.agent/workspace/`에 스킬 실행 결과 파일을 저장. 스킬은 `AGENT_WORKSPACE` 환경변수와 `cwd` 매개변수로 경로를 전달받음. `config.yaml`의 `agent.workspace_dir`로 설정 가능. 
- **서브 에이전트(Sub-Agent) 샌드박싱**: 서브 에이전트가 파일 조작이나 독립적인 연산을 수행할 경우 `workspace/sub_agents/{agent_id}/` 와 같이 시스템적으로 격리된 디렉토리(Sandbox)를 할당하여 메인 워크스페이스의 오염 방지.
- **상태 및 데이터 저장 원칙**:
  - **전역 상태 (Global State)**: `AGENT.md`는 완전 읽기 전용(드리밍 포함 어떤 프로세스도 수정 불가). `USER.md`는 드리밍 프로세스에서만 사용자 인사이트를 추가 병합. `MEMORY.md`는 드리밍에서 기억 요약을 추가.
  - **연속성 보장 (Persistence)**: `MEMORY.md`, `HEARTBEAT.md`는 SQLite를 접목하거나 버저닝이 자동 적용된 로컬 파일 포맷으로 지속/점진적으로 기록.

### 4.2. 시맨틱 메모리 및 지식 그래프 운영 방식
- **데이터베이스 (Backend 인프라):** 무거운 외부 도커 컨테이너(FalkorDB 등) 설치 구성 배제 및 의존성 제로화를 위해, 파이썬 기반 로컬 패키지만으로 경량 구동되는 **Cognee + SQLite 바인딩 Vector DB**를 1순위 구성으로 채택하여 지식 인덱싱 처리.
- **지식 파이프라인 프로세스**: 
  - **추출 (Extraction)**: 메신저 대화, 시스템 활동 로그, 워크플로 실행 결과 등에서 핵심 정보 및 주체 간 관계를 추출해 지식 노드(Knowledge Nodes)로 변환.
  - **보관 (Storage)**: Heartbeat 틱 발현 시점이나 개별 작업 완료 단계에서 로컬 메모리 DB로 일괄 Flush하여 실시간 색인율 유지.
  - **검색 및 RAG 활용**: 주 에이전트가 새로운 태스크에 착수하기 전, 연관된 키워드나 작업 맥락을 RAG 시스템에 쿼리하여 `System Prompt` 형태로 Context Window에 자동 로드(프리페치).

### 4.3. 기반 기술 및 추가 명확화 사항 (Considerations)
- **코어 구동 방식 (Daemon & Loop)**: 무거운 작업 큐(Celery 등)의 도입을 지양하고, 시스템 구조를 단순화하기 위해 `asyncio` 이벤트 루프와 강력하고 가벼운 `APScheduler` 기반의 데몬(Daemon)으로 상주.
- **개발 언어 및 환경 설정**: 코어 로직은 전면 **Python(파이썬)**으로 구현하며, 모든 구동 환경 변수는 `config.yaml` 또는 `.env` 파일을 통해 주입받아 모듈 확장성을 확보.
- **메신저 채널 보안 접근 제어**: 복잡한 로그인 연동을 배제하고 설정 파일 내 지정된 소유자의 텔레그램 식별자(User ID / Chat ID)를 화이트리스트 방식으로 매칭. 비인가 접근 시도는 패킷 선에서 즉시 DROP.
- **에이전트 제어 및 명시적 보안 (Permission Scope)**: 복잡한 내부 통신 프로토콜 규격(gRPC 등) 대신 범용성 높은 **표준 JSON 규격 텍스트** 입출력을 채택. 메인이 서브를 호출할 땐 무조건 권한 정보(허용 디스크 범위, Network IO 활성 상태) 파라미터를 주입하여 컨테이너 없이도 소프트-샌드박스를 강제.
- **민감 정보 관리 (Secret Management)**: 환경 변수(`.env`) 또는 자체 보안 관리 도구(Vault)를 이용해 토큰과 API 키를 관리. 파일 저장 접근 권한 통제를 병행하여 평문 유출 차단. **subprocess 실행 시 API 키, 토큰 등 민감 환경변수를 자동 스트리핑**하여 스킬/레시피 스크립트에 시크릿 노출 차단.
- **명령 실행 안전성 (Command Execution Safety)**: LLM이 생성한 셸 명령을 실행하기 전 **패턴 기반 위험 명령 감지(35개+ regex)**로 차단. 모든 subprocess는 **프로세스 그룹 격리(`os.setsid`)**로 실행하여 timeout 시 좀비 프로세스 방지. Hermes Agent의 보안 모델을 참고.
- **로깅 및 가시성 (Logging & Telemetry)**: 실행 결과와 에이전트의 Reasoning(사고) 과정을 `.logs/execution_YYYYMMDD.log` 형태로 아카이빙. CLI 또는 웹 대시보드 환경에서 토큰 소비량, 에러 발생 빈도 등 가시적 지표 확인 체계 지원.

## 5. 아키텍처 및 참고 사례 (References)

- [**Goose (aaif-goose/goose):**](https://github.com/aaif-goose/goose)
  - Recipe 기반의 작업 파이프라인 관리 (yaml 형식).
  - 다중 프로바이더(Anthropic, OpenAI, Google) 및 MCP 확장을 통한 로컬 에이전트 아키텍처.
- [**Hermes Agent (NousResearch/hermes-agent):**](https://github.com/nousresearch/hermes-agent)
  - Cron 작업, 학습 루프(실행 중 경험 학습), 장기 기억(Memory) 관리 체계.
  - LLM 모델 핫스왑 기능.
  - **명령 실행 보안**: `tools/approval.py`의 위험 명령 패턴 감지, `tools/environments/local.py`의 환경변수 시크릿 스트리핑 및 `preexec_fn=os.setsid` 프로세스 격리 패턴 참고.
  - **ReAct 추론 루프**: `run_agent.py`의 반복적 tool_calls → 결과 확인 → 추가 호출 루프 아키텍처를 ReAct(Thought/Action/Observation/Answer) 패턴으로 구현.
- [**Paperclip (paperclipai/paperclip):**](https://github.com/paperclipai/paperclip)
  - 여러 에이전트(OpenClaw, Claude 등)를 조직화하고 백그라운드에서 오케스트레이션하는 기능.
  - 다양한 CLI 터미널 환경 기반 업무 지시 및 상태 관리.

## 6. 단계별 개발 계획 (Milestones)

- **Phase 1: Foundation (기반 체제 및 CLI)**
  - 페르소나 설정(`AGENT.md`, `USER.md`, `MEMORY.md`) 파싱 엔진 및 프롬프트 인젝터 개발.
  - 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑 구조 구축.
- **Phase 2: Extension & Memory (확장 도구 및 메모리 구조 통합)**
  - 로컬 전용/전역(Global/Local) 스킬 모듈 및 MCP 클라이언트 구현.
  - 전용 디렉토리(`recipes/`) 기반 자동화 Recipe 실행 엔진.
  - 로컬 벡터 DB 기반 시맨틱 메모리 연동 및 드리밍(1일 1회 백업 기반) 파이프라인 추가.
- **Phase 3: Autonomy & Automation (통신 인터페이스와 스케줄링)**
  - 데몬 프로세스 상주: 5분 주기 Heartbeat 상태 모니터링 및 Cron 배포.
  - 서브 에이전트 동적 호출 모델(최소 권한, 최대 풀 제한 적용) 완성.
  - 텔레그램 봇 채팅 채널 오픈 및 단일 Webhook 기반 이벤트 리스너 가동.
- **Phase 4: Expansion (플랫폼 고도화 체제)**
  - 음성 및 모달리티 인터페이스 (STT/TTS) 연동 지원.
  - 시스템 가시성을 높이는 성능 로그 수집 및 웹 대시보드(토큰/서브루틴 통계) 적용.
