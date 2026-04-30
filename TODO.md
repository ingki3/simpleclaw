# TODO — SimpleClaw 백로그 및 진행 상태

> 이 문서는 프로젝트의 백로그, 진행 중인 작업, 완료된 작업을 추적합니다.
> 새 작업 시작 전에 이 파일을 확인하고, 완료 시 상태를 업데이트하세요.

## 상태 기호

- `[ ]` 대기 (Backlog)
- `[>]` 진행 중 (In Progress)
- `[x]` 완료 (Done)
- `[!]` 블로커 (Blocked)
- `[-]` 취소/보류 (Cancelled)

---

## In Progress

(현재 진행 중인 작업 없음)

---

## Backlog

### 에이전트 코어
- [ ] `/dreaming` 수동 트리거 명령어 — Telegram에서 dreaming을 수동 실행할 수 있도록
- [ ] LLM 라우터 hot reload — config.yaml의 LLM 프로바이더/모델 변경 시 재시작 없이 반영
- [ ] 대화 히스토리 정리 기능 — 오래된 대화 자동 삭제 또는 아카이브
- [ ] OpenAI 호환 API 지원 — OpenAI API 형식을 따르는 서드파티 LLM(vLLM, Ollama, LM Studio 등) 연동
- [ ] Claude Code CLI 연동 — Claude Code CLI를 SimpleClaw의 백엔드 또는 도구로 활용
- [ ] Claude, ChatGPT API 지원 — Claude/ChatGPT를 기본 LLM 백엔드로 실사용 가능하도록 검증 및 보완

### 시맨틱 메모리 (spec 005, BIZ-12)
- [x] **Phase 1** — `ConversationStore` 임베딩 저장/검색 API + 단위 테스트 (PR ingki3/simpleclaw#16, merged 2026-04-26)
- [x] **Phase 2** — `EmbeddingService` 통합, `_retrieve_relevant_context()` + `_tool_loop()` 하이브리드 회상 (PR ingki3/simpleclaw#17)
- [>] **Phase 3** — 그래프형 드리밍, `semantic_clusters` 인덱스, `MEMORY.md` 마커 기반 자동 upsert, 에피소드/시맨틱 분리

### 스킬
- [ ] us-market-expert 스킬 재구축 — 실행 스크립트 추가 (현재 삭제됨)
- [ ] 스킬 실행 실패 시 자동 재시도 로직

### 레시피
- [ ] 레시피 목록 조회 명령어 (`/recipes`) — 등록된 레시피 목록 확인
- [ ] 레시피 v1(step 기반)에서 v2(instruction 기반)로 마이그레이션 가이드

### Cron
- [ ] Cron job 실행 히스토리 조회 — 최근 실행 결과 확인 명령어
- [ ] Cron job 자연어 수정 — "메일 확인 주기를 1시간으로 바꿔줘"

### 인프라
- [ ] 프로세스 매니저 도입 — systemd 또는 supervisord로 봇 안정적 운영
- [ ] 로그 로테이션 — bot.log 파일 크기 관리
- [ ] CI/CD — GitHub Actions로 테스트 자동화

### 문서
- [ ] docs/ MkDocs 또는 Docusaurus 기반 정적 사이트 생성
- [ ] 스킬 개발 튜토리얼 — 처음부터 스킬 만들기 가이드

---

## Done

### 2026-04-24 ~ 04-25

- [x] **ReAct 패턴 도입** — 멀티턴 스킬 라우터를 Thought→Action→Observation→Answer 루프로 교체
- [x] **레시피 v2 + 슬래시 커맨드** — `/recipe-name`으로 Telegram에서 즉시 실행, `instructions` 필드
- [x] **Dreaming LLM 연동** — 대화 요약을 LLM으로 처리, memory→MEMORY.md, user_insights→USER.md
- [x] **Dreaming 전용 모델 설정** — `daemon.dreaming.model` config 추가
- [x] **Cron NO_NOTIFY** — `[NO_NOTIFY]` 토큰으로 불필요한 알림 생략
- [x] **Cron 격리** — `process_cron_message()` 도입, 대화 히스토리와 분리
- [x] **Workspace 디렉토리** — `.agent/workspace/`에 스킬 파일 출력 격리, `AGENT_WORKSPACE` 환경변수
- [x] **Lazy Loading** — 페르소나, 스킬, 레시피를 매 메시지마다 디스크에서 재로드
- [x] **Smart Python 경로** — `_fix_python_path()`로 스킬 venv 자동 감지
- [x] **Telegram `/` 명령어 수신** — `~filters.COMMAND` 필터 제거
- [x] **Scheduler 리팩토링** — NO_NOTIFY + 알림 콜백을 코어 모듈(`scheduler.py`)로 이동
- [x] **run_bot.py (구 test_telegram.py) thin wrapper화** — 비즈니스 로직을 코어 모듈로 이동
- [x] **AGENT.md 코드 구조 원칙 추가** — 스크립트 비즈니스 로직 금지, 커밋 규칙, lazy loading 원칙
- [x] **호칭 규칙 설정** — AGENT.md에 "형님" 금지, 존댓말 사용 규칙 추가
- [x] **문서 전체 업데이트** — README, PRD, docs/ 11개 파일 최신 반영
- [x] **테스트 전체 통과** — 389개 테스트 (ReAct, 파싱, cron 격리, dreaming 포함)
