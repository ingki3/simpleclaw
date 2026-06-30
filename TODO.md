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

- [x] **BIZ-390: Agent Study Wiki 진화형 topic registry와 scoring/decay** — 사용자 관심사가 고정되지 않는다는 전제로, study topic 의 생성·승격·감쇠·아카이브 생애주기를 한 곳에서 관리하는 `simpleclaw.study.topic_registry` 와 단일 점수 합성기 `simpleclaw.study.scorer` 를 추가. (1) `scorer.compute_topic_score` — user_interest(0.35)/repeated_mentions(0.20)/freshness_need(0.20)/global_importance(0.15)/recency_decay(0.10) 가중합 + clamp(합=1.0 강제하는 `ScoreWeights`), 반복 언급 포화 정규화(`normalize_mentions`), 지수 감쇠 최근성(`recency_decay_factor`, 기본 반감기 168h). (2) `TopicRegistry` — `InterestSignal`(user/dreaming/news 출처)을 받아 candidate→active(min_interest_score≥0.55)→pinned(운영자 고정, sticky)→cooling(decay_after_days 14 경과)→archived(2배 경과) lifecycle. peak_score≥promote_threshold(0.70) 주제는 감쇠/아카이브 창 2배로 상시 추적. Dreaming 신호 일괄 수용(`ingest_dreaming_signals`), 뉴스 topic 의 사용자 관심 승급, archived 부활, `now_fn` 주입으로 결정적 감쇠. `Topic` 이 source_planner 의 `StudyTopic` Protocol 을 만족해 `study_targets()` 를 `plan_fetch_requests` 에 직결, `to_records()` 는 study_status 운영 도구의 topics.yaml 키와 정렬. `TopicEvolutionPolicy.from_config` 로 `study.topic_evolution` config 매핑. 단위 테스트 34개(`test_study_scorer.py` 16, `test_study_topic_registry.py` 18). 검증: `tests/unit/` 1932 passed, ruff clean. (2026-06-30)
- [x] **BIZ-395: Agent Study Wiki 운영자 조회/강제 업데이트/관찰성** — Agent Study Wiki(매일 공부해 쌓는 외부 세계 배경지식)를 운영자만 점검/조작할 수 있는 관찰성 계층을 추가. 수집 파이프라인(`simpleclaw.study`, 선행 이슈)이 아직 없어도 graceful 하게 동작하도록, on-disk 레이아웃(`topics.yaml` + `daily/*.md` + `index.sqlite`)을 직접 읽는 self-contained `StudyWikiStore`(`src/simpleclaw/agent/study_status.py`)를 두고 wiki 미구성 시 `configured=false`로 응답한다. (1) operator native tool `study_status`(scope=OPERATOR, operator_gate_required, risk=MEDIUM) — action=status(최근 run·active/stale topic·low-confidence item), topics, show, refresh(다음 daily run 재수집 플래그만 기록), archive. `tool_schemas.py`/`tool_dispatch.py`/`orchestrator._NATIVE_DISPATCH_TOOL_NAMES` 배선 + operator gate 차단. (2) Admin API route group `admin_routes/study.py`(`GET /admin/v1/study/{status,topics,topics/{id}}`, `POST .../{refresh,archive}`) — service 미주입 시 config.yaml `study.wiki_dir`로 지연 구성. low-confidence는 `index.sqlite study_items` 우선, 없으면 topic confidence 폴백. 단위 테스트 26개(`test_study_status_tool.py` 18, `test_admin_api_study.py` 8) + operator registry 회귀 갱신. 검증: `tests/unit/` 1874 passed, ruff clean. 일반 사용자 runtime tool 비노출. (2026-06-29)
- [x] **BIZ-383(검색 품질): realtime-lookup 멀티소스 본문 추출 + web_search 본문 보강** — 일정/상태성 답변에서 숫자가 틀리던 근본 원인(① web_search가 DuckDuckGo snippet만 반환, ② realtime-lookup이 네이버 SERP chrome을 1800자로 잘라 사용, ③ 초경량 모델이 빈약한 근거로 수치 환각)을 검색 품질 측면에서 봉합. (1) `realtime_lookup`: 결과 링크 발견을 DuckDuckGo HTML 엔드포인트로 전환(`discover_result_links`/`extract_result_links`+uddg 디코드), 상위 N개(기본 2) 실제 기사 본문을 `_html_to_text`(nav/header/footer chrome 제거)로 추출해 멀티소스 evidence 생성, 본문 회수 실패 시 네이버 SERP 텍스트 폴백, confidence high/medium/low + 출처별 timeline_status. (2) `builtin_tools.handle_web_search`: `body_fetcher` 주입 시 상위 결과 본문 발췌(`_fetch_search_result_body`, 차단/오류 graceful skip) 동봉, dispatch 배선. 단위 테스트 확장(`test_realtime_lookup_skill.py`, `test_web_search_tool.py`), 라이브 스모크 확인(실 기사 본문 회수). 전체 `tests/unit/` 1794 passed, ruff clean. 후속: live-fact 게이트(`_looks_like_live_fact_request`) 키워드 확장은 별도. (완료: 2026-06-27)
- [x] **BIZ-378: 운영자 승인형 restart_runtime native 도구 추가** — operator gate에서만 노출되는 `restart_runtime` native tool을 추가해 `confirm=true` + `reason` 명시 시에만 macOS LaunchAgent `kickstart -k`를 수행하고, 재시작 후 live cwd, PID 변경, Admin API health, Telegram/scheduler/dashboard flags, FD count를 JSON으로 반환하게 했다. 실패 시 `log_debug` 후속 진단 힌트를 포함하며, 일반 runtime context에서는 비노출/수동 dispatch 차단된다. 단위 테스트 6개(`tests/unit/test_restart_runtime_tool.py`)와 registry/operator dispatch gate 회귀 업데이트 포함. 검증: `tests/unit/` 1748 passed, `uv run --extra dev --with ruff python -m ruff check src/` 통과. live config/recipe/skill 파일 수정 없음. (2026-06-14)
- [x] **BIZ-377: 운영자용 skill_validate native 도구 추가** — operator/development gate에서만 노출되는 `skill_validate` native tool을 추가해 configured skills local/global dir 기준 runtime skill discovery, SKILL.md metadata, `script_path` 추론, script/venv python runner 존재 여부를 read-only JSON으로 검증할 수 있게 했다. 기본은 smoke 실행 없이 읽기 전용이며, `smoke=True`일 때만 짧은 `--help` 실행을 timeout/redaction과 함께 수행한다. 단위 테스트 8개(`tests/unit/test_skill_validate_tool.py`)와 registry/operator dispatch gate 회귀 업데이트 포함. 검증: `tests/unit/` 1742 passed, `uv run --extra dev --with ruff python -m ruff check src/` 통과. (2026-06-14)
- [x] **BIZ-375: 운영자용 deploy_status native 도구 추가** — operator scope에만 노출되는 `deploy_status` native tool을 추가해 live checkout의 branch/HEAD, origin/main 또는 origin/dev ahead/behind, dirty paths와 deploy range overlap, origin/main..origin/dev unreleased commit, open PR summary를 read-only JSON으로 확인할 수 있게 했다. `compare=main|dev`, `include_prs` 옵션을 지원하고, `gh` 실패 시 git-only summary로 graceful fallback한다. 단위 테스트 5개(`tests/unit/test_deploy_status_tool.py`)와 registry/operator gate 회귀 업데이트 포함. 검증: `tests/unit/` 1727 passed, `uv run --extra dev --with ruff python -m ruff check src/ tests/unit/test_deploy_status_tool.py tests/unit/test_operator_tool_registry.py` 통과. (2026-06-14)

- [x] **BIZ-376: 운영자용 recipe_validate native 도구 추가** — operator/development gate에서만 노출되는 `recipe_validate` native tool을 추가해 configured `recipes.dir` 기준 recipe `name`/`path` resolve, YAML parse/필수 필드 오류 JSON화, empty/provided `render_params` smoke, `/cron`·`/undo` slash collision warning을 read-only로 확인할 수 있게 했다. 단위 테스트 7개(`tests/unit/test_recipe_validate_tool.py`)와 registry gate 회귀 업데이트 포함. 검증: `tests/unit/` 1729 passed, `uv run --extra dev --with ruff python -m ruff check src/` 통과. live recipe 파일 수정 없음. (2026-06-14)

- [x] **BIZ-374: 운영자용 asset_inventory native 도구 추가** — operator scope에만 노출되는 `asset_inventory` native tool을 추가해 native tool registry(scope/risk/enabled), SimpleClaw runtime skill(source dir/script_path/executable), recipe path/parse status, MCP server/tool 요약, selector config를 read-only JSON으로 확인할 수 있게 했다. Hermes skill과 runtime skill 혼동 방지를 위해 skill 항목에 `source=simpleclaw_runtime_skill`을 명시한다. 단위 테스트 7개(`tests/unit/test_asset_inventory_tool.py`)와 registry/operator gate 회귀 업데이트 포함. 검증: `tests/unit/` 1722 passed, `uv run --extra dev --with ruff python -m ruff check src/` 통과. (2026-06-14)

- [x] **BIZ-373: 운영자용 log_debug native 도구 추가** — operator scope에만 노출되는 `log_debug` native tool을 추가해 `/Users/simplist/.simpleclaw-agent/default/bot.log` 기준 최근 로그, ERROR/Traceback, trace_id, tool_loop/recipe/skill/telegram/admin_api/scheduler 관련 줄을 read-only JSON으로 확인할 수 있게 했다. 토큰/API 키/긴 사용자 본문 redaction, line/pattern limit, 파일 읽기 실패 LLM-readable 오류를 포함한다. 단위 테스트 6개(`tests/unit/test_log_debug_tool.py`)와 registry/operator dispatch gate 회귀 업데이트 포함. 검증: `tests/unit/` 1715 passed, `.venv/bin/python -m ruff check src/` 통과. (2026-06-14)

- [x] **BIZ-372: 운영자용 config_inspect native 도구 추가** — operator scope에만 노출되는 `config_inspect` native tool을 추가해 `/Users/simplist/.simpleclaw/config.yaml` 기준 effective config 요약을 read-only JSON으로 확인할 수 있게 했다. `section=all|llm|agent|memory|skills|recipes|daemon|admin_api|security`, `resolve_paths`, `redact` 옵션을 지원하고, 실제 token/api_key/master_key 값은 마스킹하되 `file:admin_api_token` 같은 시크릿 참조는 보존한다. 단위 테스트 5개(`tests/unit/test_config_inspect_tool.py`)와 registry/operator gate 회귀 업데이트 포함. 검증: `tests/unit/` 1709 passed, `uv run --extra dev --with ruff python -m ruff check src/` 통과. (2026-06-14)

- [x] **BIZ-370: 운영자 native tool scope registry 추가** — native function calling tool registry에 scope/risk/operator gate metadata를 추가하고, 기본 runtime context에서 operator/development 도구가 노출되지 않도록 회귀 테스트를 보강했다. `tests/unit/test_operator_tool_registry.py` 추가, `test_tool_schemas.py` scope filtering 보강, 전체 `tests/unit/` 1529 green + `ruff check src/` 통과. (2026-06-14)

- [x] **BIZ-243: Recipe loader 미지원 키 무성 폴백 + v1 PROMPT step silent no-op 봉합** — 2026-05-18 cron-krstock-auto 사고(`tool:`/`prompt:`/`args:` 등 비슷한 이름의 미지원 키가 무성 폴백되어 빈 content PROMPT step 2개로 파싱, scheduler 가 빈 prompt_output 으로 LLM 호출을 스킵, "Recipe completed: 2/2" 빈 통지가 텔레그램 발송) 봉합. (1) `loader.py` `_VALID_STEP_KEYS = {type, name, content, on_error, rollback}` 화이트리스트 도입, 미지원 키와 type=prompt+빈/공백 content 를 `RecipeParseError` 로 즉시 실패. (2) `scheduler.py._execute_action` v1 분기에 안전망 — recipe.steps 에 PROMPT 가 있는데 prompt_output 이 비면 WARN 로그(silent no-op 재발 시 즉시 운영자 인지). (3) `executor.py` 모듈 docstring 갱신: 호출자 책임 약속을 BIZ-243 사고 맥락과 함께 명시. 단위 테스트 11개 추가(`test_recipe_loader.TestStrictStepValidation` 7개 — 미지원 키/prompt-alias/빈·공백·누락 content/COMMAND 빈 허용/유효 키 통과; `test_scheduler.TestRecipeCronInvokesLLM` 4개 — v1 PROMPT step LLM 호출/COMMAND+PROMPT 합쳐 LLM 호출/COMMAND-only 폴백/합성 빈 PROMPT WARN 안전망). `docs/feature-recipes.md` 에 v1 스텝 허용 키와 호출자 책임 섹션 추가. 전체 `tests/unit/` 1168 green (2026-05-18)
- [x] **BIZ-202: 레시피 디렉터리를 `~/.simpleclaw/recipes/` 로 이전 — 봇/데몬 절대 경로 통일** — 봇이 채팅에서 작성한 레시피가 `~/.simpleclaw/workspace/.agent/recipes/` 에 떨어지고 데몬은 working tree CWD 기준 `.agent/recipes/` 를 봐서 등록 누락되던 사고(2026-05-15 krstock) 봉합. `config.yaml` 에 `recipes.dir` 키 신설(기본 `~/.simpleclaw/recipes`) + `load_recipes_config()`, `discover_recipes(recipes_dir, legacy_dir=…)` 가 primary→legacy 폴백 + 한 번 deprecation 경고, `CronScheduler.__init__` 가 `recipes_dir`/`legacy_recipes_dir` 받아 `_execute_action` 의 하드코드 `.agent/recipes/{name}/recipe.yaml` 제거, `AgentOrchestrator` 가 부팅 시 `recipes_dir` 절대 경로 풀이 + `try_recipe_command` 에 주입, `run_bot.py` 가 `load_recipes_config` 결과를 `CronScheduler` 에 wiring. 마이그레이션 스크립트 `scripts/migrate_recipes_dir.py`(dry-run 기본, `--apply`/`--symlink`/`--force`) 로 기존 4개(`ai-report`/`check-email`/`krstock`/`morning-briefing`) 1회 이전. AGENT.md 의 Directories 섹션 갱신(`~/.simpleclaw/recipes/<name>/recipe.yaml` 명시 + 레거시 deprecation). 단위 테스트 14개(`test_config.TestLoadRecipesConfig` 5개, `test_recipe_loader.TestDiscoverWithLegacyFallback` 5개, `test_recipes_dir_wiring` 5개, `test_scheduler.TestRecipeDirResolution` 4개) (2026-05-15)
- [x] **BIZ-190: wikidocs.net/npmjs.com SPA 회수 max-iter 잔존 패턴 봉합** — BIZ-187 패치(180s 화이트리스트 + composite 분해 가이드) 적재 후에도 wikidocs.net 4건 + npmjs.com 1건이 `web_fetch` 짧은 응답(Cloudflare 차단 페이지, 27~202 chars) 받은 뒤 `agent-browser`/`cli`/`execute_skill` 우회 시도로 max-iter(10) 소진. 봉합 3종: (1) `handle_web_fetch` 가 정적+헤드리스 양 경로 결과를 `_looks_like_block_page`(< 400 chars 또는 cloudflare/just-a-moment 시그니처) 로 검사해 `FETCH_BLOCKED:` 마커로 합성 응답, (2) `_execute_command` 가 ``agent-browser`` + ``&&``/``||``/``;`` composite chain 을 subprocess 진입 전 차단하고 단일-호출 안내 반환, (3) `_tool_loop` 가 같은 turn 안의 ``agent-browser`` 호출 횟수를 카운트해 cap(2) 초과 시 합성 응답으로 즉시 종결. `_GUARD_WEB_FETCH_PREFERRED` 에 `FETCH_BLOCKED:` 마커 대응 + 재시도 금지 한 줄 추가. 단위 테스트 16개(`test_agent.py` 4개: 블록 페이지 감지/시그니처/짧은 본문/회귀 가드; `test_orchestrator_skill_dispatch.py` 9개: composite 판별·블록·call invoker·프롬프트 가드; `test_orchestrator_tool_loop.py` 2개: cap 초과·이내 dispatch) (2026-05-13)
- [x] **BIZ-167: agent-browser `networkidle` 60초 timeout 봉합** — `_fetch_headless` 의 wait 단계를 `--load networkidle` (timeout 30s) → `--load load` (timeout 8s) 로 교체해 wikidocs.net 등 background polling 이 끊임없이 도는 SPA 에서 wait 가 영영 settle 안 되는 사고를 차단. timeout 시에도 `get text body` 가 호출돼 부분 본문 회수 동작은 그대로 유지. `_TOOL_USAGE_INSTRUCTION` 에 `_GUARD_WEB_FETCH_PREFERRED` 한 줄 추가 — 본문 회수는 `web_fetch` 가 디폴트, `agent-browser` composite 명령은 상호작용(클릭/폼/스크린샷) 한정, 부득이 호출 시 `wait --load load` 사용을 명시. 회귀 가드 단위 테스트 3개(`test_wait_uses_load_strategy_not_networkidle`, `test_wait_timeout_does_not_block_text_retrieval`, `test_tool_usage_instruction_prefers_web_fetch_over_agent_browser`) (2026-05-12)
- [x] **BIZ-16: Subprocess 좀비 프로세스 정리 및 타임아웃 보강** — `kill_process_group` SIGKILL 폴백 검증, `waitpid(WNOHANG)` 회수, 그룹 잔존 폴링, 좀비/누수 메트릭, 행 걸린 자식 통합 테스트, `MetricsCollector` 운영 배선(`run_bot.py`) + 대시보드 카드 노출 (2026-05-01)
- [x] **BIZ-29: 임베딩/클러스터 색인 분포 모니터링 및 토큰 절감 측정** — `ConversationStore` 분포 헬퍼(`count_with_embedding` / `embedding_dimension_distribution` / `cluster_member_counts` 등), `simpleclaw.memory.stats`(분포·RAG 로그 집계), `scripts/inspect_memory.py` CLI(텍스트/JSON), `_retrieve_relevant_context()` 구조화 로그(`rag_retrieve` action), 대시보드 `/api/memory_stats` + Memory Index 카드 (2026-05-01)
- [x] **BIZ-25: 구조화 로깅에 Trace ID 도입** — `simpleclaw.logging.trace_context`(contextvars 기반 trace_id 발급/전파/주입), `LogEntry.trace_id` 필수 필드 + 자동 컨텍스트 채택, `process_message`/`process_cron_message` 진입점 발급, 스킬 executor·서브에이전트 spawner의 `SIMPLECLAW_TRACE_ID` 환경변수 전파, 대시보드 `/api/logs?trace_id=…` 필터 + `/api/trace` 타임라인 + Trace Timeline 카드 (2026-05-01)
- [x] **BIZ-15: DB 스키마 마이그레이션 시스템 구축** — `simpleclaw.db.MigrationRunner`(파일 기반 SQL, schema_version 메타 테이블, 베이스라인 흡수, 적용 전 자동 백업·실패 시 원복), conversations/daemon DB의 베이스라인 0001 마이그레이션 도입, `ConversationStore`/`DaemonStore` `__init__`이 부팅 시 자동 적용, 단위 테스트 16개(`tests/unit/test_db_migrations.py`) + GitHub Actions `unit-tests.yml` CI 워크플로 (2026-05-01)
- [x] **BIZ-18: `.env` / API 키 시크릿 매니저 통합** — `simpleclaw.security.secrets`(환경변수/OS keyring/Fernet 암호화 파일 백엔드, ``"scheme:name"`` 참조 문법, 마스터 키 자동 생성 0600 권한·`SIMPLECLAW_MASTER_KEY` 우선), `config.py`의 `api_key`/`bot_token`/`auth_token` 자동 해소(레거시 평문 호환 + 경고 로그), 마이그레이션 스크립트(`scripts/migrate_secrets.py`: `.env`→keyring/file 일괄 이전 + config.yaml 자동 치환), `keyring`/`cryptography` 의존성 추가, `config.yaml.example`/README 가이드 갱신, 단위 테스트 41개(`tests/unit/test_secrets.py`) (2026-05-01)
- [x] **BIZ-24: Webhook 페이로드 크기 제한 및 Rate Limiting** — `WebhookServer`에 `max_body_size`(`Content-Length` 사전 검사 + aiohttp `client_max_size` 이중 안전망, 초과 시 413), 토큰/IP별 슬라이딩 윈도우 rate limit(0이면 비활성, 초과 시 429 + `Retry-After`), `max_concurrent_connections` + `queue_size` 동시성 게이트(포화 시 503 + `Retry-After`) 도입. 비정상 트래픽(연속 차단·단일 IP 폭주·큐 포화)에 대한 알림 콜백 + 쿨다운, `WebhookMetrics` 스냅샷, 차단 사유 `AccessAttempt` 기록 + 선택적 `StructuredLogger` 연계(`webhook_block`/`webhook_alert`). `config.yaml.example`에 신규 키와 튜닝 가이드, 단위 테스트 4종(413/429/503/알림 경로) 추가 (2026-05-01)
- [x] **BIZ-21: 스킬 실행 실패 시 자동 재시도 정책** — `RetryPolicy`(SKILL.md 프론트매터 `retry:` 블록에서 파싱), 멱등성 가드(`idempotent=True` 필요), 지수 백오프(`initial`/`factor`/`max_backoff_seconds`), 타임아웃 재시도 옵트인(`retry_on_timeout`), `execute_skill`에 재시도 루프 + `SkillResult.attempts`, `MetricsCollector`에 `skill_retries`/`skill_retry_recovered`/`skill_retry_exhausted` 카운터 (2026-05-01)
- [x] **BIZ-43: Admin UI 공통 기반 — API 클라이언트 + Layout primitives** — `web/admin/src/lib/api/`(fetchAdmin: Bearer 토큰 자동 주입을 Next 서버측 프록시 라우트 `/api/admin/[...path]`에 위임 + POST/PUT/PATCH Idempotency-Key 자동 부여 + 401/403/422/5xx/네트워크 통합 `AdminApiError`, `useAdminQuery`/`useAdminMutation` SWR 훅, `dryRun(area, patch)` 헬퍼 — BIZ-41 `?dry_run=true` 응답 그대로, `useUndo`/`registerUndo` 5분 윈도우 슬롯), `web/admin/src/components/primitives/`(Modal: focus-trap+ESC, Drawer: slide-from-right, Toast: success/info/warn/destructive-soft + AdminApiError 자동 라우팅 + Undo 액션 슬롯, ConfirmGate: 텍스트 일치 입력 + alertdialog 시맨틱, RestartStepper: 5단계 stepper 모달), CommandPalette ⌘K — 화면 11개 + 설정 키 16개 + 시크릿 메타(이름만) 검색 + ↑/↓ Enter 키보드 흐름. Storybook stories 6종, Vitest+MSW 단위 테스트 18개(`src/lib/api/__tests__/`), DESIGN.md 부록 C(SWR 채택) (2026-05-03)
- [x] **BIZ-48: Admin UI Cron 화면 구현** — `web/admin/src/app/cron/page.tsx`를 Cron 운영 화면으로 채움(잡 테이블·검색/상태 필터·빈 상태·dim 비활성·circuit-break 배지). 표현식 입력기 `_components/ExpressionInput.tsx`(5필드 cron 검증 + 다음 5회 실행 시각 미리보기, 한국어 인라인 에러), 실행 이력 Drawer `RunHistoryDrawer`(최근 20건 + stdout/stderr 펼침), 새 잡 생성 모달 `NewJobModal`(이름/표현식/액션/활성, dry-run 후 저장 게이트). `ConfirmGate` 통과 후 ``Run now`` + 토스트 결과, 삭제는 잡 이름 입력 게이트. BIZ-43가 합류하기 전까지 모달/드로어/토스트/컨펌 게이트 primitives는 `_primitives/`에 라우트-비공개로 동거, 데이터 계층은 `lib/cron/{types,expression,client}.ts`(mock 클라이언트, BIZ-41 백엔드 합류 시 `fetchAdmin` 교체) (2026-05-03)
- [x] **BIZ-52: Admin UI Logging & Traces 화면 구현** — `web/admin/src/app/logs/page.tsx`를 로그 스트림 화면으로 채움. 레벨(debug/info/warn/error)·모듈(`action_type` substring)·자유 텍스트 검색 필터, 1초 폴링 자동 새로고침 토글(시각적 fade-in 강조), `VirtualLogList`로 1만+ 항목 가상 스크롤, "더 보기" 단계 limit 증가(200씩), 행 클릭 시 `TraceDrawer`로 같은 trace_id로 묶인 항목 타임라인 + 선택 항목 JSON 원문(복사 버튼). 모든 필터·trace_id를 URL 쿼리스트링에 동기화해 깊은 링크 공유 가능. `LogLevelControl`은 `/admin/v1/config/logging` PATCH 자리를 미리 마련 — 백엔드 미합류(404) 시 "준비 중 (BIZ-37 후속)" 배지로 명시. `web/admin/src/lib/api/logs.ts` 단위 테스트 15개 (2026-05-03)
- [x] **BIZ-54: Admin UI System 화면 구현** — `web/admin/src/app/system/page.tsx`를 5개 카드(시스템 정보·재시작 액션·서브시스템 헬스·config 스냅샷·테마)로 채움. 신규 백엔드 엔드포인트 `GET /admin/v1/system/info`(version/build_sha/PID/uptime/config·DB 경로/디스크 사용량) 추가. 재시작은 ConfirmGate(`RESTART`/`RESTART-PROCESS` 토큰 일치) → RestartStepper(5단계) → `/admin/v1/system/restart` 호출 흐름. config 덤프는 백엔드 `_mask_secrets`가 적용된 채로 노출(읽기 전용 + 복사 버튼). 헬스는 5초 폴링 `/admin/v1/health` + LLM은 `/admin/v1/config/llm`의 default 프로바이더 유효성으로 약식 판정. 테마 카드는 기존 `ThemeProvider`(라이트/다크/시스템) 라디오. 테스트: 백엔드 `tests/unit/test_admin_api.py`에 system_info 인증·필수 키 케이스 2개, 프런트 `web/admin/src/app/system/_components/__tests__/format.test.ts` 6개 (2026-05-03)
- [x] **BIZ-41: Admin Backend REST API + 감사 + dry-run** — `simpleclaw.channels.admin_api.AdminAPIServer`(aiohttp, `127.0.0.1:8082`, Bearer 토큰 인증, `/admin/v1/{config,secrets,audit,logs,health,system}` 엔드포인트), `admin_audit.AuditLog`(JSONL 일별 로테이션, 시크릿 키 자동 마스킹, ID/필터 검색), `admin_policy`(Hot/Service-restart/Process-restart 분류 + 영역별 검증), Process-restart 변경은 `~/.simpleclaw/admin/pending_changes.yaml`로 적재 후 `/system/restart`에서 머지, 시크릿 reveal 15s TTL nonce, 마스터 키 회전 + 모든 file 시크릿 재암호화, undo 라운드트립으로 새 audit 항목 생성. 단위 테스트 40개(`tests/unit/test_admin_api.py`) + 통합 테스트 6개(`tests/integration/test_admin_audit.py`) (2026-05-03)
- [x] **BIZ-55: Admin UI a11y · 성능 측정 + Lighthouse 95+ 보강** — `Shell`에 "본문으로 건너뛰기" skip-link(`sr-only` → 포커스 시 좌상단 노출, `<main id="main-content" tabIndex={-1}>` 타깃) 도입, `web/admin/lighthouserc.json`(11개 라우트 측정, a11y `error >= 0.95` / 성능·BP `warn` / LCP·CLS·TBT 임계 어설션), `.github/workflows/admin-lighthouse.yml`(treosh/lighthouse-ci-action@v12 — `web/admin/**` 변경 시만 트리거, 임시 공개 스토리지 보고서 + PR 코멘트), `npm run lhci` 로컬 진입점, DESIGN.md §10 부록 D(키보드 시나리오 5종 + VoiceOver 5점검 + 회귀 차단 흐름) 추가 (2026-05-03)
- [x] **BIZ-132: persona/dreaming 라이브 파일 untrack 사고 재발 방지** — `simpleclaw.memory.safety_backup.SafetyBackupManager`(매 dreaming 사이클의 preflight 직전 위험 파일 9종 + DB 2종을 `.agent/_safety_backup/{ts}/` 에 통째 스냅샷, 보존 정책 `최근 7개 + 최근 1 always`, SQLite 는 `Connection.backup` API 로 atomic 복사), `DreamingPipeline._preflight_protected_sections` 좁은 자가 복원(파일 *부재* 한정 1회 — safety backup → legacy `memory-backup/` 폴백, 마커 손상은 그대로 abort), 복원 발생 시 `dreaming_runs.jsonl` 의 `details.recovered_from` 기록 + WARN 로그, `scripts/run_bot.py` 매니저 배선. 단위 테스트 21개(`test_safety_backup.py` 14개 + `test_dreaming_self_restore.py` 7개) (2026-05-05)
- [x] **BIZ-138: dreaming 경로 회귀(`.agent/` → `~/.simpleclaw/`) 봉합** — `scripts/run_bot.py` 의 SafetyBackupManager wiring 이 BIZ-133 후에도 ``.agent/AGENT.md`` 등 9 종 + ``.agent/_safety_backup`` backup_root 를 하드코드로 들고 있어, config.yaml 이 ``~/.simpleclaw/`` 를 가리키는데도 봇이 ``.agent/`` 에 빈 백업 디렉터리만 만들고 dreaming preflight 가 ``.agent/MEMORY.md`` 를 못 찾아 abort 하던 회귀를 봉합. 페르소나 4 종은 ``persona_local_dir / "X.md"`` 로, sidecar/HEARTBEAT/DB/backup_root 는 모두 config 키(``daemon.dreaming.{insights,suggestions,blocklist,runs}_file``, ``daemon.status_file``, ``daemon.dreaming.safety_backup_dir``) + ``_expand`` 헬퍼로 라우팅. 회귀 가드 단위 테스트 8개(`tests/unit/test_bot_wiring_paths.py` — 소스 리터럴 검사 2 + config 기본값 검사 3 + wiring 시뮬레이션 검사 2 + override 라운드트립 1), `.agent/` 잔여물 격리 스크립트 `scripts/cleanup_legacy_agent_dir.py`(dry-run 기본 + ``--apply``, 라이브 파일·디렉터리·마이그레이션 사이드카만 격리, ``skills/``·``recipes/`` 보존) + 단위 테스트 5개(`tests/unit/test_cleanup_legacy_agent_dir.py`) (2026-05-06)
- [x] **BIZ-76: Cron/Recipe 메시지 코퍼스 분리** — `ConversationMessage.channel`에 표준 prefix 규약(`recipe:<name>`, `cron:`, `cron-admin`) 도입 + 단일 분류기 `is_auto_trigger_channel` (`memory/models.py`), orchestrator 가 `/cron *` 응답을 `cron-admin`, `/<recipe>` 산출물을 `recipe:<name>` 으로 태깅 (`agent/orchestrator.py`, `agent/commands.py`), DreamingPipeline 코퍼스 로더에 3-mode 필터(`exclude` 기본 / `downweight` stride sampling / `include` 레거시) — 알 수 없는 모드는 fail-closed `exclude` 폴백, weight 클램프 [0,1], 시간순 보존. DoD: auto-trigger only 코퍼스 → `run()` 이 None 반환 + LLM 미호출. 단위 테스트 18개(`tests/unit/test_dreaming_cron_recipe_filter.py`) (2026-05-03)

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
- [>] **BIZ-172: Feature PR 자동 base-sync** — `.github/workflows/pr-base-sync.yml`(on push: dev) — base=dev 인 OPEN PR 의 behind_by 조회, ≥ 1 이면 `needs-rebase` 라벨 + 1회 안내 코멘트, 따라잡으면 라벨 자동 제거. `merge/main-to-dev-*` 패턴은 제외. 라벨은 워크플로가 멱등 생성(`gh label create --force`), 권한 `pull-requests: write` + `issues: write` (라벨)
- [ ] **서비스 모니터링 구성** — 1) 대시보드(`/api/metrics`) 임계치 알림(텔레그램), 2) `process_group_leaks > 0` 또는 좀비/자식 PID 단조 증가 시 자동 경보, 3) BIZ-16 후 1주 집중 관찰을 자동 스크립트로 대체. (BIZ-16에서 분리 — 후속 이슈로 등록)

### 문서
- [ ] docs/ MkDocs 또는 Docusaurus 기반 정적 사이트 생성
- [ ] 스킬 개발 튜토리얼 — 처음부터 스킬 만들기 가이드

---

## Done

### 2026-06-29

- [x] **BIZ-386: Agent Study Wiki 설계 문서 + config skeleton** — 사용자 메모리(USER.md/MEMORY.md/insights)와 외부 세계 배경지식을 분리하는 경계를 세웠다. `docs/agent-study-wiki.md`(목적/비목표, 메모리 vs Wiki 대비표, 데이터 모델, freshness/confidence·topic 진화 정책), `src/simpleclaw/config_sections/study.py`(`_STUDY_DEFAULTS` + 중첩 재귀 병합 `load_study_config()`, `wiki_dir` Path 정규화), `config.py` facade export, `config.yaml.example` `study:` 예시, `tests/unit/test_study_config.py`(5 케이스). study runner/wiki 생성/retrieval 연동은 후속 이슈(2/11~). 검증: `pytest tests/unit/test_study_config.py` 5 passed, config 회귀 126 passed, `ruff check` clean. (2026-06-29, BIZ-398로 전용 브랜치 분리·PR)

### 2026-06-17

- [x] **BIZ-380: Cron one-shot 알림 메타데이터와 실행 컨텍스트 가드 추가** — cron native tool schema에 `run_once`/`max_runs`/`expires_at` metadata를 노출하고, `handle_cron_action()`이 one-shot metadata를 검증·정규화해 `CronScheduler.add_job()`으로 전달하도록 했다. `process_cron_message()` 경로는 구조적 context flag로 cron mutation(add/remove/enable/disable)을 차단하며 `list`는 유지한다. 회귀 테스트: `tests/unit/test_agent.py -k 'cron'`, `tests/unit/test_tool_schemas.py`, 전체 `tests/unit/` 통과. (2026-06-17)

### 2026-06-13

- [x] **BIZ-368: MCP tools capability gate 추가** — MCP 서버 initialize 결과의 `tools` capability를 확인한 뒤에만 `list_tools()`를 호출하도록 수정해 prompt-only/resource-only 서버를 연결 실패로 오인하지 않게 했다. tools capability가 없는 서버는 connected server로 기록하되 loaded tools 0개로 로그를 남기고, initialize 실패와 tools/list 실패 메시지를 구분한다. 회귀 테스트 3개 추가(`tests/unit/test_mcp_client.py`: prompt-only 미호출 연결, tools capability 도구 등록 유지, tools/list 실패 시 다른 서버 계속 연결) (2026-06-13)
- [x] **BIZ-366: Telegram `/undo N` 대화 컨텍스트 되돌리기** — `messages.deleted_at` soft-delete 마이그레이션(0004) 추가, `ConversationStore.hide_recent_user_turns()` 및 기본 context 조회 숨김 처리(`get_recent`/`get_since`/ID 포함 조회는 감사용 include 옵션 유지), `AgentOrchestrator.process_message()`에서 LLM/tool loop 전에 `/undo`/`/undo N` 선처리. `/undo` 명령 자체는 대화 이력에 저장하지 않고, 잘못된 N/되돌릴 user turn 없음 케이스는 즉시 안내한다. 단위 테스트: store soft-delete/감사 조회/2턴 rewind + orchestrator 명령 파싱/저장 정책/invalid/empty 케이스 추가. 검증: `tests/unit/` 1675 passed, `uvx ruff check src/` passed. (2026-06-13)

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
