<!--
Sync Impact Report
- Version change: 0.0.0 → 1.0.0
- Added principles:
  - I. Python-Only Core
  - II. Lightweight Dependencies
  - III. Configuration-Driven Flexibility
  - IV. Explicit Security & Permission Scope
  - V. Test-After Implementation
  - VI. Persona & Memory Integrity
  - VII. Extensibility via Isolation
- Added sections:
  - Technical Constraints
  - Development Workflow
  - Governance
- Templates requiring updates:
  - .specify/templates/plan-template.md — ✅ aligned (Constitution Check section exists)
  - .specify/templates/spec-template.md — ✅ aligned (no constitution-specific references needed)
  - .specify/templates/tasks-template.md — ✅ aligned (phase structure compatible)
- Follow-up TODOs: none
-->

# SimpleClaw Constitution

## Core Principles

### I. Python-Only Core
모든 코어 로직은 Python으로 구현한다. 셸 스크립트, Node.js 등
보조 언어는 빌드/배포 자동화에 한정하며, 런타임 핵심 경로에
다른 언어를 도입해서는 안 된다.

- 에이전트 데몬, 스케줄러, 메모리 파이프라인, LLM 라우터 등
  모든 핵심 모듈은 Python 패키지로 작성한다.
- 외부 CLI 도구(`claude cli`, `goose` 등)는 서브프로세스로
  호출하되, 호출부 래퍼 역시 Python으로 구현한다.

### II. Lightweight Dependencies
무거운 외부 인프라 의존성을 배제하고, 로컬에서 단독 구동
가능한 경량 스택을 유지한다.

- 작업 큐는 Celery 등을 사용하지 않으며, `asyncio` 이벤트
  루프와 `APScheduler`로 대체한다.
- 데이터베이스는 Docker 컨테이너가 필요한 외부 DB를 배제하고,
  `SQLite` 및 Python 기반 로컬 패키지(Cognee 등)만 사용한다.
- 스킬별 독립 가상환경(venv) 구축을 최소화하고, `uv run` 등의
  인라인 의존성 관리를 우선한다.

### III. Configuration-Driven Flexibility
시스템 동작의 변경은 코드 수정이 아닌 설정 파일을 통해
이루어져야 한다.

- 환경 변수는 `config.yaml` 또는 `.env`를 통해 주입한다.
- LLM 라우팅은 자동 분배(Auto-Router)를 배제하고,
  `config.yaml`에 사용자가 명시한 규칙 기반 수동 라우팅을
  원칙으로 한다.
- Heartbeat 주기(기본 5분), 서브 에이전트 최대 수(기본 3),
  드리밍 시각 등 운영 파라미터는 Config에서 조정 가능해야 한다.

### IV. Explicit Security & Permission Scope
모든 에이전트 및 외부 접점에 명시적 권한 경계를 적용한다.

- 서브 에이전트는 기동 시 허용 디스크 경로, 네트워크 IO
  활성 여부 등의 권한 파라미터를 반드시 주입받아야 한다.
- 텔레그램 접근 제어는 설정 파일 내 화이트리스트(User ID /
  Chat ID) 매칭 방식을 사용하며, 비인가 요청은 즉시 차단한다.
- API 키, 토큰 등 민감 정보는 `.env` 또는 Vault를 통해
  관리하며, 코드 내 하드코딩을 금지한다.

### V. Test-After Implementation
구현 완료 후 반드시 테스트를 수행하여 동작을 검증한다.

- 각 feature 구현이 끝나면 기능 테스트를 실행한다.
- 테스트 실패 시 최대 3회까지 수정·재시도하며, 3회 초과 시
  사람에게 에스컬레이션한다.
- 테스트 결과가 불분명한 경우 사람의 확인을 받은 뒤 진행한다.

### VI. Persona & Memory Integrity
페르소나 문서와 기억 저장소의 무결성을 보장한다.

- `AGENT.md`, `USER.md`는 읽기 전용으로 취급하되,
  드리밍 프로세스 등 통제된 권한 하에서만 제한적 병합을 허용한다.
- 드리밍 실행 전 반드시 `.bak` 백업 파일을 생성하여
  환각(Hallucination)으로 인한 오염 시 즉각 롤백할 수 있도록 한다.
- `MEMORY.md`는 대화 원시 로그가 아닌, 드리밍이 도출한
  핵심 요약본만을 보존한다.

### VII. Extensibility via Isolation
확장 기능은 코어를 오염시키지 않는 격리된 구조로 제공한다.

- 스킬은 전역(Global: `~/.agents/skills/`)과
  로컬(Local: `.agent/skills/`)로 이원화하며,
  동일 이름 시 로컬이 우선(Override)한다.
- 레시피는 스킬과 격리된 전용 디렉토리(`.agent/recipes/`)에
  `recipe.yaml`로 관리한다.
- 서브 에이전트는 `workspace/sub_agents/{agent_id}/`에
  샌드박싱하여 메인 워크스페이스 오염을 방지한다.

## Technical Constraints

- **런타임**: `asyncio` 이벤트 루프 + `APScheduler` 데몬으로
  상주하며, 무거운 워커 프레임워크를 도입하지 않는다.
- **에이전트 간 통신**: JSON-over-Stdout 표준 텍스트 입출력을
  채택하며, gRPC 등 별도 프로토콜 인프라를 두지 않는다.
- **서브 에이전트 제한**: 동시 실행 최대 3개(Config 조정 가능),
  Short-lived 원칙으로 임무 완수 후 즉시 종료한다.
- **로깅**: `.logs/execution_YYYYMMDD.log` 형식으로 실행 결과와
  Reasoning 과정을 아카이빙한다.

## Development Workflow

- Spec Driven Development를 따른다: specify → clarify → plan →
  checklist → tasks → analyze → implement 순서로 진행.
- `PROGRESS.md`를 통해 Phase별 진행 상태를 추적하며,
  미완료 항목이 남아있으면 다음 사이클을 자동 반복한다.
- 계획이 불분명하면 사람에게 확인한 뒤 진행한다.
  (`--dangerously-skip-permissions` 사용 시 자율 판단 허용)
- 구현 완료 후 `PRD.md`에 변경 내역을 기록한다.

## Governance

- 본 Constitution은 프로젝트의 모든 설계 및 구현 결정에
  우선한다. 원칙과 충돌하는 구현은 정당한 사유와 함께
  Complexity Tracking 테이블에 기록해야 한다.
- 원칙의 수정은 PRD 변경을 수반해야 하며, 변경 사유,
  영향 범위, 마이그레이션 계획을 문서화한다.
- 모든 PR/코드 리뷰 시 본 Constitution 준수 여부를 확인한다.

**Version**: 1.0.0 | **Ratified**: 2026-04-17 | **Last Amended**: 2026-04-17
