# SimpleClaw Admin UI — 요건 명세 (BIZ-37)

> 부모 이슈: [BIZ-36] [SimpleClaw] 설정 Admin 화면 구축
>
> 본 문서는 Admin UI가 다루어야 할 **설정 카탈로그·노출/편집 정책·권한 모델·검증·감사·UX 흐름**을 정리한 1차 요건이다.
> 후속 작업: BIZ-38(디자인 시스템), BIZ-39(화면 설계), 그리고 프론트/백엔드 구현 이슈가 본 문서의 결정을 입력으로 삼는다.

---

## 0. 운영 컨텍스트

| 항목 | 결정 | 근거 |
|---|---|---|
| 배포 형태 | 로컬 단일 데몬 (개인용) | PRD §1, §3 — 개인 비서 전제 |
| 사용자 수 | 1명 (운영자 = 본인) | `telegram.whitelist` 단일 운영자 패턴 |
| 호스팅 | `127.0.0.1:8081`(대시보드)·`127.0.0.1:8080`(웹훅) 등 로컬 바인딩 우선 | `config.yaml.example` 기본값 |
| 서비스 가용성 | 24/7 데몬, 핫리로드 우선·재시작 최소화 | PRD §3.1, §4.3 |
| 신뢰 경계 | 로컬머신 사용자 = 풀 권한. 외부 노출 시 reverse proxy + auth 가정 | 보안 모델 단순화 |

본 Admin UI의 핵심 가치는 *“설정 파일을 직접 안 만져도 모든 운영 손잡이를 안전하게 돌릴 수 있다”* 이다.

---

## 1. 설정 카탈로그

`config.yaml.example`, `src/simpleclaw/`의 각 모듈, PRD를 교차해 노출 대상을 12개 영역으로 묶었다.
열은 **노출**(read 가능), **편집**, **적용 정책**, **마스킹**, **검증**.

| # | 영역 | 키/소스 | 노출 | 편집 | 적용 | 마스킹 | 검증 |
|---|---|---|---|---|---|---|---|
| 1 | **LLM 라우터** | `llm.default`, `llm.providers.{claude,openai,gemini}` | ✅ | ✅ | hot-reload (다음 호출부터) | `api_key` 시 마스킹 | `default ∈ providers`; 모델명 화이트리스트(프로바이더별); API ping(선택) |
| 2 | **Agent 코어** | `agent.history_limit`, `db_path`, `max_tool_iterations`, `workspace_dir` | ✅ | ✅(경로 외) | 즉시(다음 메시지) | — | 정수 범위(`history_limit 1–200`, `max_tool_iterations 1–20`); 경로는 read-only |
| 3 | **시맨틱 메모리(RAG)** | `memory.rag.{enabled, model, top_k, similarity_threshold}` | ✅ | ✅ | 토글 ON 시 모델 다운로드 발생(~500MB), 그 외 즉시 | — | `top_k 1–20`, `threshold 0.0–1.0`; 모델 변경 시 인덱스 재생성 경고 |
| 4 | **Security / CommandGuard** | `security.command_guard.{enabled, allowlist}`, `security.env_passthrough` | ✅ | ✅ | 즉시 | env 키 이름은 노출 OK, 값은 *주입 안 함* | allowlist 패턴 키는 사전 정의 set과 매칭; `env_passthrough` 항목은 시크릿 패턴(`*_KEY` 등)과 충돌 시 경고 |
| 5 | **Skills** | `skills.{local_dir, global_dir, execution_timeout}` + 디스커버리 결과 | ✅ | 디렉토리 read-only / timeout 편집 | timeout 즉시, 디렉토리 변경은 재시작 필요 | — | timeout 5–600s; 디렉토리 존재 여부 |
| 6 | **MCP 서버** | `mcp.servers` (이름 → command/env/args) | ✅ | ✅ (CRUD) | 재시작 필요 | env 값 마스킹 | command 절대경로 또는 PATH 내 존재; args 배열; env value는 시크릿 참조 권장 |
| 7 | **Voice (STT/TTS)** | `voice.stt.*`, `voice.tts.*` | ✅ | ✅ | 즉시 | — | 모델 enum, `speed 0.25–4.0`, `max_text_length ≤ 4096`, `output_format ∈ {mp3,opus,aac,flac}` |
| 8 | **Telegram 채널** | `telegram.bot_token`, `telegram.whitelist.{user_ids, chat_ids}` | ✅ | ✅ | 토큰 변경은 봇 재시작 필요(자동 트리거 OK), allowlist는 즉시 | 토큰 마스킹 + 회전 액션 | 토큰 정규식 `^\d+:[\w-]{20,}$`; user_id/chat_id 정수 |
| 9 | **Webhook 채널** | `webhook.{enabled, host, port, auth_token, max_body_size, rate_limit, rate_limit_window, max_concurrent_connections, queue_size, alert_cooldown}` | ✅ | ✅ | host/port 변경 시 재시작; rate-limit·body·concurrency·alert는 즉시 | `auth_token` 마스킹 + 회전 | 정수 범위; `max_body_size ≤ 16MB`; port 1024–65535; |
| 10 | **Sub-Agents** | `sub_agents.{max_concurrent, default_timeout, workspace_dir, cleanup_workspace, default_scope.{allowed_paths, network}}` | ✅ | ✅ | 즉시(신규 스폰부터) | — | `max_concurrent ≤ 10`(하드캡); 경로 화이트리스트 |
| 11 | **Daemon / Heartbeat / Dreaming / Wait / Cron** | `daemon.heartbeat_interval`, `daemon.pid_file`, `daemon.status_file`, `daemon.db_path`, `daemon.dreaming.{overnight_hour, idle_threshold, model}`, `daemon.wait_state.default_timeout`, `daemon.cron_retry.*` | ✅ | ✅(경로 제외) | heartbeat·dreaming·cron_retry·wait_state는 즉시; 경로는 재시작 필요 | — | `heartbeat_interval ≥ 60s`; `overnight_hour 0–23`; `idle_threshold ≥ 600s`; `max_attempts 1–10`; `backoff_strategy ∈ {linear, exponential}`; `circuit_break_threshold ≥ 0` |
| 12 | **Persona** | `persona.{token_budget, local_dir, global_dir, files[*]}` + `AGENT.md`/`USER.md`/`MEMORY.md`/`SOUL.md` 내용 | ✅ | 파일 내용 ✅ / 메타 ✅ / 경로 ❌ | 파일 변경은 hot-reload(매 메시지) | — | `token_budget 512–32000`; 파일 type enum |

### 1.1 데이터 영역 (설정 + 운영 데이터 혼합)

다음은 “설정”은 아니지만 운영자가 자주 보고 싶어하는 항목으로, Admin UI 1차 범위에 포함한다.

| 영역 | 소스 | 화면 |
|---|---|---|
| Cron 작업 목록·실행 히스토리 | `daemon.db` (`cron_jobs`, `cron_runs`) | Cron 화면 |
| 메모리 인덱스/클러스터 통계 | `agent.db_path` (`messages`, `semantic_clusters`) | Memory 화면 |
| 시크릿 키 일람 | `~/.simpleclaw/secrets.enc` + keyring scope + env 화이트리스트 | Secrets 화면 |
| 구조화 로그 / trace timeline | `.logs/execution_YYYYMMDD.log`, `.logs/structured.jsonl` | Logging 화면 |
| 메트릭 스냅샷 | `http://127.0.0.1:8081/api/metrics` | Dashboard 카드 |
| 감사 로그 | (신규) `.agent/audit.jsonl` | Audit 화면 |

---

## 2. 노출/편집 정책 결정 매트릭스

상세 결정은 §1 표에 포함되어 있으며, 본 절에서는 **공통 규칙**만 정리한다.

### 2.1 적용 정책 등급

| 등급 | 의미 | 표시 |
|---|---|---|
| `Hot` | 다음 메시지/호출부터 자동 반영 (페르소나·라우터 모델·webhook rate-limit 등) | 초록 dot, “즉시 적용” 라벨 |
| `Service-restart` | 해당 서브시스템 재시작 필요 (텔레그램 봇 재기동, MCP 서버 재기동 등) — 버튼 클릭으로 트리거 가능 | 노란 dot + “서브시스템 재시작 필요(자동)” |
| `Process-restart` | 데몬 전체 재시작 필요 (호스트·포트·DB 경로 등) | 빨간 dot + “데몬 재시작 필요(수동/자동)” |

UI 결정: **편집 폼 저장 직전에 등급 칩을 노출**하고, restart 등급은 confirm 모달.

### 2.2 마스킹 규칙

* `*api_key`, `*_token`, `*password`, `*_secret`, 시크릿 참조 문법(`env:*`, `keyring:*`, `file:*`)의 *해석된 값*은 **항상 마스킹**(`••••1234` 형태로 마지막 4자만).
* 시크릿 참조 문자열(`keyring:claude_api_key`)은 평문 노출 OK — 그 자체가 비밀이 아니다.
* 마스킹 해제(reveal) 액션은 **별도 권한 토글**이 필요하고 30초 후 자동 재마스킹.
* `Copy` 액션은 마스킹 해제 없이 동작 (클립보드에 raw value).

### 2.3 검증 정책

| 종류 | 처리 |
|---|---|
| **클라이언트 검증** | 타입/범위/정규식. 입력 즉시 폼 인라인 에러. |
| **서버 검증** | 백엔드 `POST /api/admin/validate` 가 동일 스키마로 재검증 + 외부 의존(예: API ping, 디렉토리 존재) 체크. |
| **변경 dry-run** | 모든 “Hot” 변경은 최소 1회 dry-run 결과(diff 카드)를 보여준 뒤 commit. |

### 2.4 시크릿 참조 자동완성

라우터/채널/webhook의 시크릿 입력 필드에는 **시크릿 참조 빌더**를 제공한다.
* 백엔드(env / keyring / file vault)별 사용 가능한 키 리스트를 드롭다운.
* 신규 키 등록 액션은 모달 → 즉시 vault에 기록.

---

## 3. 권한 모델

### 3.1 1차 결정: 단일 운영자

본 릴리스(BIZ-36)는 **단일 운영자(=로컬 사용자) 가정**으로 출발한다. 이유:
* PRD가 “개인 비서” 모델을 명확히 함.
* 데몬·webhook·텔레그램 모두 단일 owner 화이트리스트.
* 다중 사용자/RBAC 도입은 도메인을 늘리고 인증 인프라를 요구하므로 BIZ-36 범위에서 제외.

### 3.2 그래도 분리해 둘 “쓰기 권한 게이트”

다중 사용자가 아니더라도, **위험 카테고리에 한해 한 번의 추가 확인**을 강제한다. 이는 “자기 자신의 실수”를 막기 위한 인터랙션 게이트이지 RBAC가 아니다.

| 카테고리 | 게이트 |
|---|---|
| 시크릿 변경/회전 | 텍스트 confirm (“rotate webhook_auth_token” 입력) |
| 데몬 재시작 / 데이터베이스 경로 변경 | 텍스트 confirm + 5초 카운트다운 |
| 페르소나 파일 대량 삭제(>30% 줄 감소) | 변경 diff + “SAVE PERSONA” 입력 |
| Cron job 삭제 | 단일 confirm 모달 |
| Webhook 비활성화 | 단일 confirm 모달 |

### 3.3 미래 확장 여지

* 향후 다중 사용자 시 `roles: [reader, operator, admin]`을 도입할 수 있도록 백엔드 API에 `actor_id`(현재는 항상 `local`) 필드를 *처음부터* 포함한다.
* 감사 로그(§4)에 `actor_id`를 함께 적재하면 RBAC 도입 시 마이그레이션 없이 확장 가능.

---

## 4. 감사 / 롤백

### 4.1 감사 로그 요구사항

모든 “설정 변경/시스템 액션”에 대해 다음을 `.agent/audit.jsonl`에 append한다.

```jsonc
{
  "ts": "2026-05-02T23:30:11+09:00",
  "actor_id": "local",
  "action": "config.update",          // config.update | secret.rotate | cron.create | service.restart | persona.write 등
  "target": "llm.providers.claude.model",
  "before": "claude-sonnet-4-20250514",
  "after": "claude-opus-4-20250514",
  "trace_id": "01HW...",              // structured logger와 join
  "outcome": "applied",               // applied | dry_run | rejected
  "reason": null
}
```

* `before`/`after`가 시크릿이면 둘 다 마스킹된 형태로 기록 (`••••1234`).
* trace_id는 구조화 로그(`.logs/structured.jsonl`)와 조인 가능해야 한다.
* JSONL은 일자별 로테이션(추후): 1차 릴리스는 단일 파일.

### 4.2 롤백(Undo)

* **즉시 롤백**: 같은 화면에서 “방금 변경한 항목 되돌리기”를 5분 이내(=undo 윈도우) 토스트로 노출.
* **이력 기반 롤백**: 감사 로그 화면에서 임의 항목 옆 ↺ → before 값으로 재적용 (서버는 새로운 audit 엔트리로 기록).
* **위험 카테고리 롤백 제한**: 시크릿 회전·데몬 재시작은 undo 불가 (대신 다시 적용 가능).

### 4.3 백업

* `AGENT.md`, `USER.md`, `MEMORY.md`, `SOUL.md` 편집은 **저장 직전 `.bak` 자동 생성** (드리밍 백업과 동일 패턴).
* `config.yaml` 편집 시 `config.yaml.{ts}.bak` 자동 생성, 최근 10개 보존.

---

## 5. UX 흐름 결정

### 5.1 검색·필터·그룹핑

* 글로벌 ⌘K 명령 팔레트: 모든 설정 키·화면·시크릿 키 이름을 검색 가능 (값은 노출 안 함).
* 각 리스트 화면은 헤더에 `Search input` + `Status filter`(예: Cron의 `running|paused|circuit_open`).
* 그룹핑은 사이드바의 영역(=§1 카탈로그 12개)으로 충분. 화면 내부는 카드로 추가 분류.

### 5.2 변경 사전 검증 (dry-run/preview)

다음 영역은 dry-run을 **필수**로 둔다.

| 영역 | dry-run 내용 |
|---|---|
| LLM 라우터 모델 변경 | 변경 후 1회 LLM ping → 응답 latency·token usage 표시 |
| Webhook rate-limit 변경 | 최근 1시간 트래픽에 새 임계치 적용 시뮬레이션 (block 추정 수) |
| Cron retry/circuit-break | 최근 30일 실패 데이터로 새 정책의 효과 시뮬레이션 |
| 페르소나 편집 | 토큰 budget 영향 (현재 → 예상) + diff |
| 시크릿 회전 | 새 토큰으로 1회 인증 ping 후 commit |

### 5.3 적용 후 헬스체크

* 변경 적용 직후 *해당 서브시스템의 health 라이트*를 강조(파동 애니메이션) → 5초 내 `green`이 되지 않으면 자동 롤백 제안 모달.
* Dashboard의 “최근 변경” 위젯에 마지막 5건의 audit + 적용 결과 노출.

### 5.4 빈 상태 / 첫 실행

* 신규 설치 직후: 사이드바의 각 영역에 “설정이 비어 있어요. 기본값으로 시작 →” CTA.
* config.yaml이 없는 환경: `config.yaml.example`을 1-step 마법사로 복사·튜닝.

### 5.5 키보드·접근성

* `Tab` 순서: 사이드바 → 헤더 → 메인 콘텐츠. 화면 내 위→아래·좌→우.
* 모든 액션은 키보드만으로 도달 가능.
* 색대비 WCAG AA 이상.
* 위험 액션 버튼은 색 + 텍스트(“삭제”/“회전”) + 아이콘 3중 표시 (색맹 안전).

### 5.6 반응형 우선순위

* 데스크톱(1280+) 우선. 태블릿 768–1279 폴백 (사이드바 → 햄버거).
* 모바일은 *읽기 전용 + 알림 응답 흐름만* 1차 범위. 본격 편집은 2차.

---

## 6. Admin 화면 인벤토리 (BIZ-39 입력)

§1·§5 결정을 화면 단위로 묶으면 다음 11개로 수렴한다.

1. **Dashboard** — LLM/Cron/Memory/Daemon health, 최근 변경, 메트릭 스냅샷, 최근 에러
2. **LLM 라우터** — 프로바이더 카드, 모델 변경, fallback 체인, ping 테스트
3. **Persona** — `AGENT.md`/`USER.md`/`MEMORY.md`/`SOUL.md` 마크다운 에디터 + 토큰 예산
4. **Skills & Recipes** — 디스커버리 결과, 활성/비활성, 실행 정책, 마지막 실행 결과
5. **Cron** — 작업 목록, 다음 실행, 재시도/circuit-break, 실행 히스토리, 트리거 액션
6. **Memory** — RAG 토글/모델, 임베딩·클러스터 통계, 인덱스 재생성, 메시지 검색
7. **Secrets** — 백엔드별 키 일람(마스킹), 회전 액션, 마스터 키 회전, 신규 키 생성
8. **Channels** — Telegram(토큰·allowlist), Webhook(rate-limit·body·concurrency·알림 콜백 상태)
9. **Logging & Traces** — 구조화 로그 검색, trace_id 타임라인, 메트릭 미리보기
10. **Audit** — 모든 설정 변경 이력, 필터, 롤백 액션
11. **System** — 데몬 주기, dreaming, wait state, sub-agent 풀, 보안 정책, 백업

---

## 7. 결정 보류 / 후속 논의 항목

* **다중 사용자(RBAC) 도입 시점** — BIZ-36 범위 외. 외부 노출(클라우드 호스팅) 의사 결정과 묶어 별도 이슈.
* **모바일 편집 UX** — 1차 범위 외, 알림 응답·heartbeat 확인 등 read 위주만 지원.
* **외부 SSO** — 단일 운영자 가정 하 미적용.
* **버전 관리(Git 연동)** — `config.yaml` git 추적은 운영자 자율. UI에서 `git diff` 노출 여부는 추후 결정.
* **MCP 서버 마켓플레이스/디스커버리** — 본 1차에서는 수동 등록만. 자동 디스커버리는 BIZ-별도 이슈로.

---

## 8. 부록: 출처 매핑

| 영역 | 1차 출처 |
|---|---|
| 카탈로그 §1 | `config.yaml.example` (lines 1–137), `src/simpleclaw/config.py` |
| 적용 정책 §2.1 | PRD §3.1 (Lazy Loading / Hot Reload), `daemon/` 모듈 |
| 마스킹 §2.2 | PRD §4.3 (시크릿 관리), BIZ-18 시크릿 매니저 |
| 감사 §4 | BIZ-19/22/24 로깅 패턴(structured logger trace_id) |
| 화면 §6 | BIZ-39 본문 인벤토리 + §1 카탈로그 머지 |

---

_이 문서는 BIZ-37의 산출물이며, BIZ-38(디자인 시스템)·BIZ-39(화면 설계)·후속 구현 이슈의 **변경 불가능한 입력**으로 사용된다. 변경이 필요하면 본 문서를 PR로 갱신한 뒤 후속 이슈를 다시 정렬한다._
