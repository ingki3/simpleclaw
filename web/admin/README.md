# SimpleClaw Admin (web/admin)

Next.js 16 (App Router) + Tailwind v4 + TypeScript로 구성한 SimpleClaw 단일 운영자용 관리 화면.
시각·상호작용 단일 진실은 저장소 루트의 `DESIGN.md`이며, 본 패키지는 그 토큰·컴포넌트·패턴을
React로 구현한 1차 스캐폴딩이다.

## 빠른 시작

```bash
cd web/admin
npm install
npm run dev          # http://localhost:3000
npm run storybook    # http://localhost:6006
npm test             # vitest run (API 클라이언트 + MSW)
```

## 환경 변수 (`.env.local`)

```bash
# 데몬에서 발급받아 keyring에 저장된 admin_api_token을 그대로 옮긴다.
# 클라이언트 번들에는 절대 포함되지 않으며, /api/admin/[...path] 프록시에서만 사용된다.
ADMIN_API_TOKEN=...

# 선택: 데몬 위치(기본 http://127.0.0.1:8082)
ADMIN_API_BASE=http://127.0.0.1:8082
```

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

* 컴포넌트는 *semantic 토큰만* 참조한다 (`bg-[--card]`, `text-[--foreground]` 등).
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
