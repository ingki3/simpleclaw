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

- [x] **BIZ-16: Subprocess 좀비 프로세스 정리 및 타임아웃 보강** — `kill_process_group` SIGKILL 폴백 검증, `waitpid(WNOHANG)` 회수, 그룹 잔존 폴링, 좀비/누수 메트릭, 행 걸린 자식 통합 테스트, `MetricsCollector` 운영 배선(`run_bot.py`) + 대시보드 카드 노출 (2026-05-01)
- [x] **BIZ-29: 임베딩/클러스터 색인 분포 모니터링 및 토큰 절감 측정** — `ConversationStore` 분포 헬퍼(`count_with_embedding` / `embedding_dimension_distribution` / `cluster_member_counts` 등), `simpleclaw.memory.stats`(분포·RAG 로그 집계), `scripts/inspect_memory.py` CLI(텍스트/JSON), `_retrieve_relevant_context()` 구조화 로그(`rag_retrieve` action), 대시보드 `/api/memory_stats` + Memory Index 카드 (2026-05-01)
- [x] **BIZ-25: 구조화 로깅에 Trace ID 도입** — `simpleclaw.logging.trace_context`(contextvars 기반 trace_id 발급/전파/주입), `LogEntry.trace_id` 필수 필드 + 자동 컨텍스트 채택, `process_message`/`process_cron_message` 진입점 발급, 스킬 executor·서브에이전트 spawner의 `SIMPLECLAW_TRACE_ID` 환경변수 전파, 대시보드 `/api/logs?trace_id=…` 필터 + `/api/trace` 타임라인 + Trace Timeline 카드 (2026-05-01)
- [x] **BIZ-15: DB 스키마 마이그레이션 시스템 구축** — `simpleclaw.db.MigrationRunner`(파일 기반 SQL, schema_version 메타 테이블, 베이스라인 흡수, 적용 전 자동 백업·실패 시 원복), conversations/daemon DB의 베이스라인 0001 마이그레이션 도입, `ConversationStore`/`DaemonStore` `__init__`이 부팅 시 자동 적용, 단위 테스트 16개(`tests/unit/test_db_migrations.py`) + GitHub Actions `unit-tests.yml` CI 워크플로 (2026-05-01)
- [x] **BIZ-18: `.env` / API 키 시크릿 매니저 통합** — `simpleclaw.security.secrets`(환경변수/OS keyring/Fernet 암호화 파일 백엔드, ``"scheme:name"`` 참조 문법, 마스터 키 자동 생성 0600 권한·`SIMPLECLAW_MASTER_KEY` 우선), `config.py`의 `api_key`/`bot_token`/`auth_token` 자동 해소(레거시 평문 호환 + 경고 로그), 마이그레이션 스크립트(`scripts/migrate_secrets.py`: `.env`→keyring/file 일괄 이전 + config.yaml 자동 치환), `keyring`/`cryptography` 의존성 추가, `config.yaml.example`/README 가이드 갱신, 단위 테스트 41개(`tests/unit/test_secrets.py`) (2026-05-01)
- [x] **BIZ-24: Webhook 페이로드 크기 제한 및 Rate Limiting** — `WebhookServer`에 `max_body_size`(`Content-Length` 사전 검사 + aiohttp `client_max_size` 이중 안전망, 초과 시 413), 토큰/IP별 슬라이딩 윈도우 rate limit(0이면 비활성, 초과 시 429 + `Retry-After`), `max_concurrent_connections` + `queue_size` 동시성 게이트(포화 시 503 + `Retry-After`) 도입. 비정상 트래픽(연속 차단·단일 IP 폭주·큐 포화)에 대한 알림 콜백 + 쿨다운, `WebhookMetrics` 스냅샷, 차단 사유 `AccessAttempt` 기록 + 선택적 `StructuredLogger` 연계(`webhook_block`/`webhook_alert`). `config.yaml.example`에 신규 키와 튜닝 가이드, 단위 테스트 4종(413/429/503/알림 경로) 추가 (2026-05-01)
- [x] **BIZ-21: 스킬 실행 실패 시 자동 재시도 정책** — `RetryPolicy`(SKILL.md 프론트매터 `retry:` 블록에서 파싱), 멱등성 가드(`idempotent=True` 필요), 지수 백오프(`initial`/`factor`/`max_backoff_seconds`), 타임아웃 재시도 옵트인(`retry_on_timeout`), `execute_skill`에 재시도 루프 + `SkillResult.attempts`, `MetricsCollector`에 `skill_retries`/`skill_retry_recovered`/`skill_retry_exhausted` 카운터 (2026-05-01)
- [x] **BIZ-43: Admin UI 공통 기반 — API 클라이언트 + Layout primitives** — `web/admin/src/lib/api/`(fetchAdmin: Bearer 토큰 자동 주입을 Next 서버측 프록시 라우트 `/api/admin/[...path]`에 위임 + POST/PUT/PATCH Idempotency-Key 자동 부여 + 401/403/422/5xx/네트워크 통합 `AdminApiError`, `useAdminQuery`/`useAdminMutation` SWR 훅, `dryRun(area, patch)` 헬퍼 — BIZ-41 `?dry_run=true` 응답 그대로, `useUndo`/`registerUndo` 5분 윈도우 슬롯), `web/admin/src/components/primitives/`(Modal: focus-trap+ESC, Drawer: slide-from-right, Toast: success/info/warn/destructive-soft + AdminApiError 자동 라우팅 + Undo 액션 슬롯, ConfirmGate: 텍스트 일치 입력 + alertdialog 시맨틱, RestartStepper: 5단계 stepper 모달), CommandPalette ⌘K — 화면 11개 + 설정 키 16개 + 시크릿 메타(이름만) 검색 + ↑/↓ Enter 키보드 흐름. Storybook stories 6종, Vitest+MSW 단위 테스트 18개(`src/lib/api/__tests__/`), DESIGN.md 부록 C(SWR 채택) (2026-05-03)
- [x] **BIZ-48: Admin UI Cron 화면 구현** — `web/admin/src/app/cron/page.tsx`를 Cron 운영 화면으로 채움(잡 테이블·검색/상태 필터·빈 상태·dim 비활성·circuit-break 배지). 표현식 입력기 `_components/ExpressionInput.tsx`(5필드 cron 검증 + 다음 5회 실행 시각 미리보기, 한국어 인라인 에러), 실행 이력 Drawer `RunHistoryDrawer`(최근 20건 + stdout/stderr 펼침), 새 잡 생성 모달 `NewJobModal`(이름/표현식/액션/활성, dry-run 후 저장 게이트). `ConfirmGate` 통과 후 ``Run now`` + 토스트 결과, 삭제는 잡 이름 입력 게이트. BIZ-43가 합류하기 전까지 모달/드로어/토스트/컨펌 게이트 primitives는 `_primitives/`에 라우트-비공개로 동거, 데이터 계층은 `lib/cron/{types,expression,client}.ts`(mock 클라이언트, BIZ-41 백엔드 합류 시 `fetchAdmin` 교체) (2026-05-03)
- [x] **BIZ-54: Admin UI System 화면 구현** — `web/admin/src/app/system/page.tsx`를 5개 카드(시스템 정보·재시작 액션·서브시스템 헬스·config 스냅샷·테마)로 채움. 신규 백엔드 엔드포인트 `GET /admin/v1/system/info`(version/build_sha/PID/uptime/config·DB 경로/디스크 사용량) 추가. 재시작은 ConfirmGate(`RESTART`/`RESTART-PROCESS` 토큰 일치) → RestartStepper(5단계) → `/admin/v1/system/restart` 호출 흐름. config 덤프는 백엔드 `_mask_secrets`가 적용된 채로 노출(읽기 전용 + 복사 버튼). 헬스는 5초 폴링 `/admin/v1/health` + LLM은 `/admin/v1/config/llm`의 default 프로바이더 유효성으로 약식 판정. 테마 카드는 기존 `ThemeProvider`(라이트/다크/시스템) 라디오. 테스트: 백엔드 `tests/unit/test_admin_api.py`에 system_info 인증·필수 키 케이스 2개, 프런트 `web/admin/src/app/system/_components/__tests__/format.test.ts` 6개 (2026-05-03)
- [x] **BIZ-41: Admin Backend REST API + 감사 + dry-run** — `simpleclaw.channels.admin_api.AdminAPIServer`(aiohttp, `127.0.0.1:8082`, Bearer 토큰 인증, `/admin/v1/{config,secrets,audit,logs,health,system}` 엔드포인트), `admin_audit.AuditLog`(JSONL 일별 로테이션, 시크릿 키 자동 마스킹, ID/필터 검색), `admin_policy`(Hot/Service-restart/Process-restart 분류 + 영역별 검증), Process-restart 변경은 `~/.simpleclaw/admin/pending_changes.yaml`로 적재 후 `/system/restart`에서 머지, 시크릿 reveal 15s TTL nonce, 마스터 키 회전 + 모든 file 시크릿 재암호화, undo 라운드트립으로 새 audit 항목 생성. 단위 테스트 40개(`tests/unit/test_admin_api.py`) + 통합 테스트 6개(`tests/integration/test_admin_audit.py`) (2026-05-03)

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

### 레시피
- [ ] 레시피 목록 조회 명령어 (`/recipes`) — 등록된 레시피 목록 확인
- [ ] 레시피 v1(step 기반)에서 v2(instruction 기반)로 마이그레이션 가이드

### Cron
- [ ] Cron job 실행 히스토리 조회 — 최근 실행 결과 확인 명령어
- [ ] Cron job 자연어 수정 — "메일 확인 주기를 1시간으로 바꿔줘"
- [x] **BIZ-19: Cron 작업 실패 시 자동 재시도** — `CronJob`에 작업별 재시도 정책(max_attempts/backoff_seconds/backoff_strategy/circuit_break_threshold) + `consecutive_failures` 카운터, `CronJobExecution.attempt` 컬럼으로 재시도별 실행 기록 분리, `execute_job()` 재시도 루프(linear/exponential 백오프), 누적 실패 임계값 도달 시 자동 비활성+알림 콜백, `enable_job()`이 카운터 리셋 (2026-05-01)

### 인프라
- [ ] 프로세스 매니저 도입 — systemd 또는 supervisord로 봇 안정적 운영
- [ ] 로그 로테이션 — bot.log 파일 크기 관리
- [x] **CI/CD** — GitHub Actions로 PR/푸시 시 단위 테스트(+DB 마이그레이션) 자동 실행 (BIZ-15에 포함, 2026-05-01)
- [ ] **서비스 모니터링 구성** — 1) 대시보드(`/api/metrics`) 임계치 알림(텔레그램), 2) `process_group_leaks > 0` 또는 좀비/자식 PID 단조 증가 시 자동 경보, 3) BIZ-16 후 1주 집중 관찰을 자동 스크립트로 대체. (BIZ-16에서 분리 — 후속 이슈로 등록)

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
