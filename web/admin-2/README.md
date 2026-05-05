# SimpleClaw Admin 2.0 (web/admin-2)

Next.js 16 (App Router) + Tailwind v4 + TypeScript 로 구성한 SimpleClaw Admin 2.0
재구축 트리의 **S0 스캐폴드** (BIZ-111). 기존 `web/admin/` 은 점진 교체가 아닌
나란히 둔 별도 디렉터리에서 처음부터 다시 쌓는다 (BIZ-108 본문 명시).

이후 단계는:

| 단계 | 이슈 | 내용 |
| --- | --- | --- |
| S0 | BIZ-111 | 본 스캐폴드 — build/lint/test 인프라 |
| S1 | (분해 예정) | Design System — admin.pen 토큰 import + 컴포넌트 디렉터리 |
| S2 | (분해 예정) | App Shell — Sidebar/Topbar 합성, 다크 모드 토글 wiring |

디자인 SSOT 는 저장소 루트의 `admin.pen` 의 `ThIdV` (Light) / `tY3NP` (Dark) /
`pJlmh` (BIZ-63 Dark Mode 보정안) 프레임. S1 에서 본 패키지 안으로 토큰을 주입한다.

## 빠른 시작

```bash
cd web/admin-2
npm install
npm run dev          # http://localhost:8089
npm run lint
npm run test         # vitest run (단위 스모크)
npm run build        # 프로덕션 빌드
```

> **포트 정책** — Multica 웹(`:3000`), 기존 Admin(`:8088`)과 충돌하지 않도록
> Admin 2.0 은 기본 포트를 `:8089` 로 고정한다. 다른 포트를 쓰려면 `PORT`
> 환경변수로 오버라이드한다.
>
> ```bash
> PORT=8200 npm run dev     # http://localhost:8200
> PORT=8200 npm start       # 빌드 결과를 :8200 에서 서빙
> ```
>
> 포트 분리 로직은 `scripts/run-next.mjs` 에서 처리하며, macOS/Linux/Windows 에서
> 동일하게 동작한다.

## E2E (Playwright)

```bash
npm run test:e2e:install   # 최초 1회 — Chromium 다운로드 (sandbox 환경에서는 실패할 수 있음)
npm run build              # next start 를 webServer 블록에서 띄우므로 사전 빌드 필요
npm run test:e2e
```

`playwright.config.ts` 의 `webServer` 가 `node scripts/run-next.mjs start` 를
띄우므로 외부 데몬은 필요 없다. CI 에서는 `web/admin-2/.github/workflows/admin-2-ci.yml`
의 `e2e` 잡이 같은 흐름을 자동화한다.

## 구조

```text
web/admin-2/
  src/
    app/
      layout.tsx       — RootLayout (S0 최소 형태)
      page.tsx         — hello-world (S0 marker)
      globals.css      — Tailwind v4 import 만 (토큰은 S1)
    components/        — S1 부터 atoms/molecules/... 채움
  scripts/
    run-next.mjs       — 포트 wrapper (기본 8089)
  tests/
    unit/              — vitest (jsdom + @testing-library/react)
    e2e/               — playwright 스모크
  next.config.ts
  postcss.config.mjs
  tsconfig.json
  vitest.config.ts
  playwright.config.ts
  .eslintrc.json
```

## 디자인 토큰 import 합의 (S0 → S1 인계)

S1 에서 토큰은 다음 규칙으로 추가한다.

1. `src/app/globals.css` 의 `@theme` 블록에 Light 토큰을 정의 (admin.pen `ThIdV`).
2. `.theme-dark` 셀렉터 아래에 Dark 토큰을 같은 이름으로 재정의 (admin.pen `tY3NP` + `pJlmh`).
3. 컴포넌트는 *semantic 토큰만* 참조한다 — Tailwind v4 의 CSS 변수 단축 문법
   `(--var)` 만 사용 (예: `bg-(--card)`, `text-(--foreground)`). v3 시절의
   대괄호 형태(`bg-[--card]`)는 v4 에서 invalid CSS 가 emit 되어 transparent 로
   떨어진다 (BIZ-82 회귀). 기존 `web/admin/README.md` 의 동일 규약을 따른다.
4. 라이트/다크 분기는 globals.css 의 토큰 두 셋으로만 처리한다 — 컴포넌트
   내부 분기 0 줄.
