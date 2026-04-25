# SimpleClaw Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-18

## Active Technologies
- Python 3.11+ + `apscheduler>=3.10` (scheduling), existing `simpleclaw` modules (memory, recipes) (006-heartbeat-cron-scheduler)
- SQLite (cron jobs, execution logs, wait states) via existing patterns + HEARTBEAT.md status file (006-heartbeat-cron-scheduler)

- Python 3.11+ + `markdown-it-py` (마크다운 파싱), `tiktoken` (토큰 카운팅), `PyYAML` (config.yaml 로드) (001-persona-parser-engine)

## Project Structure

코드베이스의 구조와 모듈 간 관계는 `graphify-out/` 디렉토리의 분석 결과를 참고한다.

```text
src/simpleclaw/
  agent/          — 오케스트레이터, 도구 스키마, 내장 도구, 명령어 처리
  llm/            — LLM 라우터, 프로바이더(Gemini/Claude/OpenAI), Native Function Calling
  persona/        — 페르소나 파서, 어셈블러, 리졸버 (AGENT.md/USER.md/MEMORY.md)
  skills/         — 스킬 디스커버리, 실행기, MCP 클라이언트
  recipes/        — 레시피 로더, 실행기
  memory/         — 대화 저장소, 드리밍 파이프라인
  daemon/         — 데몬, 하트비트, 크론 스케줄러, 대기 상태
  security/       — CommandGuard, 환경변수 필터, 프로세스 격리
  channels/       — Telegram 봇, 웹훅 서버
  voice/          — STT/TTS 인터페이스
  logging/        — 구조화 로거, 메트릭, 대시보드
  agents/         — 서브 에이전트 풀, 스포너, 워크스페이스
  config.py       — 설정 로더 (config.yaml → 각 서브시스템)
tests/
  unit/           — 단위 테스트 (334개)
  integration/    — 통합 테스트
graphify-out/
  graph.json      — 코드 지식 그래프 (1707 nodes, 7069 edges, 33 communities)
  graph.html      — 인터랙티브 시각화
  GRAPH_REPORT.md — 분석 리포트
```

## Commands

```bash
# 전체 테스트
.venv/bin/python -m pytest tests/

# 단위 테스트만 (빠름, CI 필수)
.venv/bin/python -m pytest tests/unit/

# 특정 모듈 테스트
.venv/bin/python -m pytest tests/unit/test_agent.py -v

# 린터
.venv/bin/python -m ruff check src/
```

## Code Style

Python 3.11+: Follow standard conventions

## 주석 작성 규칙

코드 주석은 한국어로 작성하며, 다음 3단계로 구분한다.

### 1. 파일 레벨 (모듈 docstring)
- 파일 최상단에 `"""..."""` 형식으로 작성
- 모듈의 역할, 주요 동작 흐름, 설계 결정(예: hot-reload 정책)을 기술
- 외부에서 이 파일을 처음 접하는 개발자가 전체 맥락을 파악할 수 있어야 함

### 2. 함수/메서드 레벨 (docstring)
- 모든 public/private 메서드에 한국어 docstring 작성
- 한 줄 요약 + (필요 시) 상세 설명, Args, Returns 포함
- "무엇을 하는가"보다 "왜 이렇게 하는가"에 중점

### 3. 인라인 주석 (코드 라인)
- 로직의 의도가 코드만으로 불명확한 곳에만 추가
- `# 왜(why)` 중심으로 작성, `# 무엇(what)` 반복은 지양
- 분기·예외 처리·보안 체크 등 판단 근거가 필요한 곳에 집중

## Recent Changes
- 006-heartbeat-cron-scheduler: Added Python 3.11+ + `apscheduler>=3.10` (scheduling), existing `simpleclaw` modules (memory, recipes)

- 001-persona-parser-engine: Added Python 3.11+ + `markdown-it-py` (마크다운 파싱), `tiktoken` (토큰 카운팅), `PyYAML` (config.yaml 로드)

<!-- MANUAL ADDITIONS START -->
## 개발 작업 시 참고
- AGENT.md 파일의 지침을 참고할 것
- TODO.md 파일의 백로그와 진행 상태를 참고할 것

## TODO.md 관리 규칙

`TODO.md`는 프로젝트의 백로그/진행/완료 상태를 추적하는 단일 소스 오브 트루스(SSOT)이다.

1. **작업 시작 전**: TODO.md의 Backlog에서 작업을 확인하고, `[>]`로 변경하여 In Progress 섹션으로 이동
2. **작업 완료 시**: `[x]`로 변경하여 Done 섹션으로 이동, 완료 날짜 기록
3. **새 작업 발견 시**: Backlog 섹션에 `[ ]`로 추가
4. **블로커 발생 시**: `[!]`로 변경하고 사유를 주석으로 기록
5. **커밋 시**: TODO.md 변경사항도 함께 커밋

## 테스트 계층

| 계층 | 경로 | 목적 | API 키 필요 |
|------|------|------|-------------|
| 단위 테스트 | `tests/unit/` | 개별 모듈 로직 검증 | 아니오 |
| 통합 테스트 | `tests/integration/` | 모듈 간 연동 검증 | 일부 |
| 시나리오 테스트 | `tests/test_*_scenarios.py` | 실제 사용 시나리오 | 예 |
| E2E 테스트 | `tests/test_e2e_*.py` | 전체 파이프라인 | 예 |

## 테스트 작성 규칙

1. **새 기능 추가 시 반드시 단위 테스트 동반**
2. **LLM 호출이 필요한 테스트는 router를 mock** — `orchestrator._router.send = AsyncMock(...)`
3. **Native Function Calling mock 사용** — `response.tool_calls = [ToolCall(...)]` 또는 `response.tool_calls = None` (텍스트 응답)
4. **스킬 실행 테스트는 subprocess를 mock**
5. **async 테스트는 `@pytest.mark.asyncio` 필수**
6. **기능 변경 후 반드시 `pytest tests/unit/` 통과 확인** 후 전체 테스트 실행
7. **Cron 테스트는 `process_cron_message()` 사용** — 대화 히스토리와 격리됨

## Agent 실행

```bash
# 포그라운드
.venv/bin/python scripts/run_bot.py

# 백그라운드
nohup .venv/bin/python scripts/run_bot.py > .agent/bot.log 2>&1 &
```

## Git 브랜치 관리 규칙

**반드시 아래 워크플로우를 따를 것. 예외 없음.**

### 브랜치 구조
```
feature/xxx  ──(PR)──>  dev  ──(PR)──>  main
```

### 규칙
1. **`main`과 `dev`에 직접 push 금지** — 반드시 PR을 통해서만 merge
2. **모든 작업은 feature branch에서 수행**:
   - `dev`에서 분기: `git checkout dev && git checkout -b feature/작업명`
   - 작업 완료 후 `origin`에 push: `git push -u origin feature/작업명`
   - PR 생성: `feature/작업명` → `dev`
3. **`dev` → `main` 릴리스**:
   - dev에서 충분히 검증된 후 PR 생성: `dev` → `main`
4. **커밋 메시지**: 변경 사항을 명확히 요약, 한글 또는 영문
5. **PR 생성 시**: `gh pr create`로 생성, Summary와 Test plan 포함

## graphify 코드 분석 규칙

**코드베이스 구조 파악 시 반드시 graphify 결과물을 먼저 참고한다.**

### 코드 구조 파악
1. **새 작업 시작 전**: `graphify-out/graph.json`과 `graphify-out/GRAPH_REPORT.md`를 확인하여 관련 모듈과 의존 관계를 파악
2. **모듈 간 관계 질의**: `/graphify query "질문"` 또는 `/graphify path "모듈A" "모듈B"`로 그래프를 탐색
3. **특정 개념 이해**: `/graphify explain "개념명"`으로 노드와 연결 관계 확인

### 코드 변경 후 그래프 갱신 (필수)
**코드 변경 작업이 완료되면 반드시 `/graphify . --update`를 실행하여 지식 그래프를 최신화한다.**

1. **기능 추가/수정/삭제 후**: `/graphify . --update` 실행 — 변경된 파일만 재추출 (incremental)
2. **대규모 리팩토링 후**: `/graphify .` 실행 — 전체 재분석
3. **갱신 후 확인**: `graphify-out/GRAPH_REPORT.md`의 God Nodes, Surprising Connections 변화 확인
<!-- MANUAL ADDITIONS END -->
