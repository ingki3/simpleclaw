# SimpleClaw Admin (web/admin)

Next.js 16 (App Router) + Tailwind v4 + TypeScript로 구성한 SimpleClaw 단일 운영자용 관리 화면.
시각·상호작용 단일 진실은 저장소 루트의 `DESIGN.md`이며, 본 패키지는 그 토큰·컴포넌트·패턴을
React로 구현한 1차 스캐폴딩이다.

## 빠른 시작

```bash
cd web/admin
npm install
npm run dev          # http://localhost:3100
npm run storybook    # http://localhost:6006
npm test             # vitest run (API 클라이언트 + MSW)
```

> **포트 정책** — 동일 머신에서 **Multica 웹 앱이 `localhost:3000`을 점유**하므로,
> SimpleClaw Admin은 기본 포트를 `3100`으로 고정한다. Storybook은 기본값(`6006`)을 그대로 사용한다.
> 다른 포트를 쓰려면 `PORT` 환경변수로 오버라이드하면 된다.
>
> ```bash
> PORT=3200 npm run dev     # http://localhost:3200
> PORT=3200 npm start       # 빌드 결과를 :3200에서 서빙
> ```
>
> 포트 분리 로직은 `scripts/run-next.mjs`에서 처리하며, macOS/Linux/Windows에서 동일하게 동작한다.

## 환경 변수 (`.env.local`)

처음 셋업할 때는 리포 루트의 헬퍼 스크립트가 토큰 발급(`keyring:admin_api_token`),
`.env.local` 작성, `config.yaml`의 `admin_api` 블록 보강을 한 번에 처리한다.

```bash
# 리포 루트에서
.venv/bin/python scripts/setup_admin_api.py
```

스크립트는 idempotent하다 — 이미 토큰이 있으면 재사용하고, `.env.local` 의 토큰
라인만 갱신한다. 토큰을 강제로 재발급하려면 `--force`를 붙인다.

직접 작성하고 싶으면 `.env.local.example` 을 복사해 다음 키를 채워도 된다:

```bash
cp .env.local.example .env.local
```

```bash
# 기본 포트(3100) 외 다른 포트를 쓰고 싶을 때만 설정한다.
# PORT=3200

# 데몬에서 발급받아 keyring에 저장된 admin_api_token을 그대로 옮긴다.
# 클라이언트 번들에는 절대 포함되지 않으며, /api/admin/[...path] 프록시에서만 사용된다.
ADMIN_API_TOKEN=...

# 선택: 데몬 위치(기본 http://127.0.0.1:8082)
ADMIN_API_BASE=http://127.0.0.1:8082
```

> **dev 서버 재기동 필수** — `.env.local` 변경 후에는 `npm run dev` 프로세스를
> 재시작해야 새 토큰이 프록시 라우트(`/api/admin/[...path]`)에 반영된다.

> **백엔드 CORS 정합** — Admin Backend(`AdminAPIServer`, BIZ-58)는 `cors_origins`에
> `http://localhost:3100`을 기본 포함해야 한다. 위 스크립트는 `config.yaml` 에 이
> origin이 빠져 있으면 자동으로 추가한다. 다른 포트로 운영할 경우 백엔드 설정의
> `cors_origins`도 함께 추가한다.

## 검증 (백엔드 살아 있을 때)

```bash
# 데몬: scripts/run_bot.py 가 admin_api 를 :8082 에 띄운다.
curl -sS http://localhost:3100/api/admin/health        | head -c 200
curl -sS http://localhost:3100/api/admin/config/llm    | head -c 200
curl -sS http://localhost:3100/api/admin/config/persona | head -c 200
```

세 호출 모두 200 + JSON이면 11개 화면이 실데이터로 렌더된다.

## 구조

```
web/admin/
  src/
    app/
      api/admin/[...path]/    Next 서버측 프록시 — 토큰을 Bearer 헤더로 주입
      <area>/                 11개 영역 페이지 + globals.css(@theme)
    components/
      atoms/                  Button, Input, Badge, Switch, PolicyPill, StatusPill
      molecules/              SettingCard, SecretField, DryRunFooter, RestartBanner
      domain/                 ProviderCard, CronJobRow, AuditRow, MaskedSecretRow
      primitives/             Modal, Drawer, Toast, ConfirmGate, RestartStepper
      command-palette/        ⌘K — 화면/설정/시크릿 검색
      layout/                 Sidebar / Topbar / Shell / PlaceholderPage
    lib/
      api/                    fetchAdmin, useAdminQuery/Mutation, dryRun, useUndo
      setting-keys.ts         ⌘K가 검색하는 설정 키 인벤토리
      nav, theme, icon, cn    유틸
  .storybook/                 Storybook 설정 (Tailwind v4 통합)
  vitest.config.ts            jsdom + MSW 기반 단위 테스트
```

## 디자인 시스템 규약

* 컴포넌트는 *semantic 토큰만* 참조한다 — Tailwind v4 의 CSS 변수 단축 문법 `(--var)` 만 사용 (예: `bg-(--card)`, `text-(--foreground)`). v3 시절의 대괄호 형태(`bg-` + `[--card]`)는 v4 에서 `background-color: --card;` 로 invalid CSS 가 emit 되어 transparent 로 떨어진다 (BIZ-82 회귀).
* 라이트/다크 분기는 `globals.css`의 토큰 두 셋(`--*` light root + `.theme-dark`)으로만 처리한다 — **컴포넌트 내부 분기 0줄**.
* 새 컬러/간격이 필요하면 Primitive를 globals.css에 먼저 추가한 뒤 Semantic을 매핑한다.
* 아이콘은 lucide-react를 사용하고, nav 등 정적 메타데이터에서는 이름 문자열로만 보관해 `lib/icon.tsx`로 변환한다.

## 데이터 패칭

DESIGN.md 부록 C에 따라 **SWR**을 사용한다. 영역별 화면은 항상 `@/lib/api`의 export만 사용한다.

```tsx
import { useAdminQuery, useAdminMutation, dryRun } from "@/lib/api";

const { data, error, isLoading } = useAdminQuery<HealthSnapshot>("/health");
const { trigger, isMutating } = useAdminMutation("/config/llm");
await trigger({ method: "PATCH", json: patch, invalidate: ["/config/llm"] });
const preview = await dryRun("webhook", { rate_limit: 30 });
```

## 후속 작업

* 11개 영역의 실제 컨텐츠 — 각 영역별 별도 이슈로 분리.
* CommandPalette 시크릿 인덱스를 ``useAdminQuery('/secrets')``에 연결.
* RestartStepper를 `/system/restart` + 헬스 폴링과 합성한 fully-managed `<RestartFlow>`.
