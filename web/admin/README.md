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
```

## 구조

```
web/admin/
  src/
    app/                      App Router — 11개 영역 페이지 + globals.css(@theme)
    components/
      atoms/                  Button, Input, Badge, Switch, PolicyPill, StatusPill
      molecules/              SettingCard, SecretField, DryRunFooter, RestartBanner
      domain/                 ProviderCard, CronJobRow, AuditRow, MaskedSecretRow
      command-palette/        ⌘K 스켈레톤
      layout/                 Sidebar / Topbar / Shell / PlaceholderPage
    lib/                      nav, theme, icon, cn 유틸
  .storybook/                 Storybook 설정 (Tailwind v4 통합)
```

## 디자인 시스템 규약

* 컴포넌트는 *semantic 토큰만* 참조한다 (`bg-[--card]`, `text-[--foreground]` 등).
* 라이트/다크 분기는 `globals.css`의 토큰 두 셋(`--*` light root + `.theme-dark`)으로만 처리한다 — **컴포넌트 내부 분기 0줄**.
* 새 컬러/간격이 필요하면 Primitive를 globals.css에 먼저 추가한 뒤 Semantic을 매핑한다.
* 아이콘은 lucide-react를 사용하고, nav 등 정적 메타데이터에서는 이름 문자열로만 보관해 `lib/icon.tsx`로 변환한다.

## 후속 작업

* 11개 영역의 실제 컨텐츠 — 각 영역별 별도 이슈로 분리.
* CommandPalette의 설정 키/시크릿 인덱스 주입.
* Modal/Drawer/Toast/ConfirmGate 등 §3.3 Layout의 미구현 컴포넌트.
* RestartBanner의 5단계 재시작 stepper 모달.
