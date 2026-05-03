# SimpleClaw Admin — Design System (DESIGN.md)

> 부모 이슈: [BIZ-36] [SimpleClaw] 설정 Admin 화면 구축 / 본 산출물: BIZ-38
>
> 본 문서는 SimpleClaw Admin UI의 **시각적·구조적 디자인 시스템**을 정의한다.
> 짝 산출물: `admin.pen` (Pencil 워크스페이스의 시각 토큰·컴포넌트·화면 라이브러리).
>
> 이 문서가 정의하지 *않는* 것: 화면별 인벤토리·플로우(BIZ-39), 백엔드/프론트 구현(후속 이슈).

---

## 0. 사전 조사 요약

### 0.1 multica-ai/multica
* Next.js 16 (App Router) + TypeScript 프론트, Go(Chi) + PostgreSQL 17(pgvector) 백엔드, WebSocket 실시간.
* 화면 패턴: **Settings → Runtimes / Agents** 같은 *Settings 하위에 도메인별 카드/리스트* 구조.
* 멀티 워크스페이스 + 역할 기반 권한이 1차 시민. (SimpleClaw는 단일 운영자라 1:1 차용은 안 함.)
* 차용할 점: (a) 사이드바 + 컨텐츠 2-zone, (b) Settings 하위 도메인 라우팅, (c) 실시간 상태 dot.
* **버릴 점**: 멀티 테넌트/협업 UX는 SimpleClaw의 단일 운영자 가정에 비해 과한 영역 — Notification, Member 관리, RBAC 분리는 1차에 도입하지 않는다.

### 0.2 Google Stitch
* `https://stitch.withgoogle.com` — Google의 “Design with AI”. 공개 컨텐츠가 빈약해 직접 인용은 어려우나, **AI 도구가 운영 화면을 생성할 때 따라야 할 일반 원칙**(명확한 1차 액션, 상태 가시성, dry-run, undo, 위험 변경 격리)을 1순위 가이드로 차용.
* 본 시스템의 §1 “Principles”는 Stitch의 그러한 운영 원칙 + Pencil의 Web App 가이드(“Purpose First”, “Dominant Region”, “Progressive Disclosure”, “System Status Visibility”)를 통합·요약했다.

### 0.3 DESIGN.md 컨벤션 비교
* shadcn/ui, Radix Primitives, Vercel Geist, Atlassian Design System의 README/`design.md`를 비교하면 공통 섹션은 **Principles → Tokens → Components → Patterns → Accessibility → Voice & Tone** 6개. 본 문서도 동일 골격을 채택한다.

---

## 1. Principles

> SimpleClaw Admin의 모든 화면은 다음 7개 원칙으로 설계·리뷰된다. 충돌 시 위 원칙이 아래를 이긴다.

1. **Single-operator first.** 1명의 운영자가 자기 데몬을 안전하게 운용하는 것이 모든 트레이드오프의 기준. 협업·다중 사용자 가정은 도입하지 않는다.
2. **Purpose first, one screen one question.** 한 화면은 한 질문에 답한다 (예: Cron 화면은 “지금 어떤 작업이 어떤 상태로 도는가?”). 보조 액션은 secondary로 격하한다.
3. **Make state legible.** 모든 데이터·서비스는 *loading / empty / error / success / restricted* 상태를 시각적으로 갖는다. 침묵 실패 금지.
4. **Hot, then dry-run, then commit.** 모든 변경은 (a) 적용 등급(Hot/Restart) 칩, (b) dry-run preview, (c) 적용 후 health flash의 3단계로 흐른다.
5. **Risky changes are loud.** 시크릿·데몬 재시작·파일 삭제는 색·텍스트·아이콘 3중 표시 + 텍스트 confirm 게이트.
6. **Reversibility by default.** 즉시 적용된 변경에는 5분 undo 토스트, 감사 로그 화면에서 임의 시점 되돌리기. 시크릿 회전·재시작만 예외.
7. **Density is intentional.** 화면 단위로 *Compact*(테이블/로그) ↔ *Medium*(설정/카드) ↔ *Airy*(첫 진입 빈 상태) 모드를 명시한다. 한 화면 안에서 혼합 금지.

---

## 2. Design Tokens

토큰은 두 층으로 운영한다. **Primitive**(컬러 팔레트·타이포 패밀리·간격 단위)와 **Semantic**(`$--background`, `$--primary` 등 의미 토큰). 프론트는 항상 semantic 토큰만 참조한다. `admin.pen`도 동일 변수명을 사용한다(아래 §2.7 참조).

### 2.1 컬러 — Primitive (Neutral)

| 토큰 | Light | Dark | 용도 |
|---|---|---|---|
| `neutral-0` | `#FFFFFF` | `#0B0F14` | base |
| `neutral-50` | `#F7F8FA` | `#10151B` | sub bg |
| `neutral-100` | `#EEF1F5` | `#161C24` | card bg / hover |
| `neutral-200` | `#E2E6EC` | `#1F2731` | border subtle |
| `neutral-300` | `#CBD2DA` | `#2A3441` | border |
| `neutral-400` | `#9AA3AF` | `#3D4A5C` | divider strong |
| `neutral-500` | `#6B7280` | `#5A6779` | placeholder |
| `neutral-600` | `#4B5563` | `#8A95A8` | muted text |
| `neutral-700` | `#374151` | `#B6BFCE` | secondary text |
| `neutral-800` | `#1F2937` | `#D6DCE6` | primary text |
| `neutral-900` | `#0B0F14` | `#F1F4F9` | headings |

### 2.2 컬러 — Primitive (Brand & State)

| 토큰 | Light | Dark | 용도 |
|---|---|---|---|
| `brand-500` | `#5B6CF6` | `#7C8BFF` | 1차 액션·강조 |
| `brand-600` | `#4453E0` | `#5B6CF6` | hover/active |
| `brand-50` | `#EEF0FF` | `#1A2244` | tint bg |
| `success-500` | `#16A34A` | `#22C55E` | 정상/완료 |
| `success-50` | `#E7F8EE` | `#0D2B19` | tint bg |
| `warning-500` | `#D97706` | `#F59E0B` | 주의/재시도 |
| `warning-50` | `#FFF4E5` | `#2A1B05` | tint bg |
| `danger-500` | `#DC2626` | `#EF4444` | 위험/실패 |
| `danger-50` | `#FDECEC` | `#2A0F0F` | tint bg |
| `info-500` | `#0284C7` | `#38BDF8` | 정보/링크 |
| `info-50` | `#E5F4FB` | `#06243A` | tint bg |

> 톤 결정: SimpleClaw 로고/identity가 별도로 없으므로 **차분한 인디고(`#5B6CF6`)**를 1차 brand로 둔다. 향후 브랜드가 정해지면 이 한 토큰만 교체한다.

### 2.3 컬러 — Semantic (이걸 사용한다)

| Semantic 토큰 | Light → Primitive | Dark → Primitive | 의미 |
|---|---|---|---|
| `$--background` | `neutral-0` | `neutral-0` | 페이지 배경 |
| `$--surface` | `neutral-50` | `neutral-50` | 사이드바·서브 영역 |
| `$--card` | `neutral-0` | `neutral-100` | 카드 배경 |
| `$--card-elevated` | `neutral-0` | `neutral-100` | 모달·dropdown |
| `$--border` | `neutral-200` | `#232C38` (raw) | 일반 보더 — 다크 elevated 표면 보더 가시성 보강 (BIZ-64) |
| `$--border-strong` | `neutral-300` | `neutral-300` | 강조 보더 |
| `$--foreground` | `neutral-800` | `neutral-800` | 본문 텍스트 |
| `$--foreground-strong` | `neutral-900` | `neutral-900` | 헤딩 |
| `$--muted-foreground` | `neutral-600` | `neutral-600` | 보조 텍스트 |
| `$--placeholder` | `neutral-500` | `neutral-500` | 입력 placeholder |
| `$--primary` | `brand-500` | `brand-500` | 1차 액션 배경 |
| `$--primary-foreground` | `neutral-0` | `neutral-900` | 1차 액션 텍스트 |
| `$--primary-hover` | `brand-600` | `#A0AAFF` (raw) | hover — 다크 base(brand-500)보다 밝아야 hover UX가 일관됨 (BIZ-64) |
| `$--ring` | `brand-500` (40% alpha) | 동일 | focus ring |
| `$--color-success` | `success-500` | success-500 | 상태 |
| `$--color-success-bg` | `success-50` | `success-50` | 상태 tint |
| `$--color-warning` | `warning-500` | warning-500 | 상태 |
| `$--color-warning-bg` | `warning-50` | `warning-50` | 상태 tint |
| `$--color-error` | `danger-500` | danger-500 | 상태 |
| `$--color-error-bg` | `danger-50` | `danger-50` | 상태 tint |
| `$--color-info` | `info-500` | info-500 | 상태 |
| `$--color-info-bg` | `info-50` | `info-50` | 상태 tint |
| `$--destructive` | `danger-500` | danger-500 | 위험 액션 배경 |
| `$--destructive-foreground` | `neutral-0` | `neutral-900` | 위험 액션 텍스트 |
| `$--secret-mask-bg` | `neutral-100` | `#0E1218` (raw) | 마스킹된 시크릿 칩 배경 — 다크에서 카드(neutral-100)와 분리 (BIZ-64) |

### 2.4 타이포

* **Primary (`$--font-primary`)**: `Inter`, system-ui — 헤딩·라벨·내비게이션
* **Secondary (`$--font-secondary`)**: `Inter`, system-ui — 본문·입력 (동일 패밀리, weight로만 구분)
* **Mono (`$--font-mono`)**: `JetBrains Mono`, ui-monospace — 키 이름·시크릿·로그·코드

스케일:

| Token | Size | Line | Weight | 용도 |
|---|---|---|---|---|
| `text-3xl` | 32 | 40 | 600 | 페이지 타이틀 (Dashboard) |
| `text-2xl` | 24 | 32 | 600 | 화면 타이틀 |
| `text-xl` | 20 | 28 | 600 | 카드 타이틀 |
| `text-lg` | 18 | 26 | 600 | 섹션 헤더 |
| `text-md` | 16 | 24 | 500 | 라벨·강조 본문 |
| `text-base` | 14 | 22 | 400 | 본문 default |
| `text-sm` | 13 | 20 | 400 | 보조·테이블 셀 |
| `text-xs` | 12 | 16 | 500 | 칩·tag·메타 |
| `text-mono-sm` | 12 | 18 | 400 | 키/값/로그 (`$--font-mono`) |

### 2.5 간격 그리드

`4px` 베이스. 허용 값: `2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64`.
임의 값 금지 — 디자인이나 코드에서 동일 위배 시 리뷰 reject 사유.

| 컨텍스트 | gap | padding |
|---|---|---|
| 페이지 섹션 | 24–32 | — |
| 카드 그리드 | 16–24 | — |
| 폼 row | 16 (가로), 12 (세로) | — |
| 카드 내부 | — | 24 |
| 버튼 내부 | — | `[10, 16]` |
| 입력 내부 | — | `[8, 12]` |
| 사이드바 항목 | — | `[10, 16]` |
| 페이지 컨텐츠 | — | 32 |
| 모달 | — | 24 |
| Compact 테이블 셀 | — | `[8, 12]` |

### 2.6 라운딩 / 셰도우

| Token | 값 | 사용처 |
|---|---|---|
| `$--radius-none` | 0 | 테이블 셀, 페이지 헤더 stroke |
| `$--radius-sm` | 4 | 칩·tag·badge |
| `$--radius-m` | 8 | 카드, 입력, 버튼 |
| `$--radius-l` | 12 | 모달, dropdown, 큰 카드 |
| `$--radius-pill` | 9999 | toggle thumb, status pill |

| Shadow | 값 | 용도 |
|---|---|---|
| `$--shadow-sm` | `0 1px 2px rgba(11,15,20,.06)` | hover 피드백 |
| `$--shadow-m` | `0 4px 16px rgba(11,15,20,.08)` | 모달·dropdown |
| `$--shadow-l` | `0 12px 32px rgba(11,15,20,.12)` | 토스트·command palette |

### 2.7 모션

| Token | 값 |
|---|---|
| `motion-fast` | 120ms cubic-bezier(.2,.8,.2,1) |
| `motion-base` | 180ms cubic-bezier(.2,.8,.2,1) |
| `motion-slow` | 280ms cubic-bezier(.2,.8,.2,1) |

* health flash: 1× pulse `motion-slow`.
* 모달 enter: `motion-base` slide-up 8px + fade.
* `prefers-reduced-motion: reduce`이면 모든 transform 제거, opacity만 유지.

### 2.8 다크 모드 정책

* 다크 모드 **지원**한다 (운영자 야간 사용 빈도 높음).
* 토큰 모두 `{ light, dark }` 짝으로 제공. 시스템 prefers-color-scheme 기본 + manual override.
* 그래프/차트는 라인 weight를 다크 모드에서 `+0.5px` 보정.

---

## 3. Component Library (1차 인벤토리)

각 컴포넌트는 `admin.pen`에 reusable component로 등록된다. 상태 변형은 `default / hover / active / focus / disabled / error` 6종을 표준으로 한다.

### 3.1 Atomic
* **Button**: `primary | secondary | outline | ghost | destructive`. 크기 `sm | md | lg`. icon-only variant.
* **IconButton**: `md | sm`. 사각/원형.
* **Input**: text/number/password. trailing/leading icon slot.
* **Textarea**: 자동 높이.
* **Select / Combobox**: search 가능 옵션.
* **Toggle (Switch)**: `default | checked | disabled`.
* **Checkbox / Radio**: 폼 내 사용.
* **Label**: 폼 라벨, 도움말 슬롯, required/optional 마커.
* **Badge / Tag**: `neutral | success | warning | danger | info | brand`.
* **StatusPill**: dot + 라벨. 색은 semantic state.
* **SecretField**: 마스킹 표시 + reveal/copy/rotate 액션. (SimpleClaw 전용)
* **Code / Mono**: 인라인 코드, 시크릿 참조 표시(`keyring:claude_api_key`).
* **Tooltip**: 8s 안에 떠야 하는 마이크로 카피.

### 3.2 Molecular
* **InputGroup**: Label + Input + Hint + Error.
* **FormRow**: 가로 2분할(name, value).
* **PolicyChip**: `Hot | Service-restart | Process-restart` — §1 §2.1과 매칭.
* **DryRunCard**: before/after diff + 적용 버튼.
* **AuditEntry**: actor·action·target·outcome·trace_id·undo.
* **HealthDot**: `green | amber | red | grey` + tooltip.
* **MetricCard**: 라벨 + 값(big) + delta(작게) + sparkline 옵션.
* **EmptyState**: 일러스트(추후) + 본문 + CTA.
* **ConfirmGate**: 텍스트 confirm 입력 + 카운트다운 게이지.
* **MaskedSecretRow**: 키 이름 + 마스킹 값 + reveal/copy/rotate.

### 3.3 Layout
* **Sidebar**: 로고 → 영역 nav (12 항목) → footer(데몬 상태/버전).
* **TopBar**: 페이지 타이틀, breadcrumb, 글로벌 검색(⌘K), 환경 표시(local/dev/prod), 다크 모드 토글, actor.
* **PageContainer**: `padding: 32`, `gap: 24`.
* **TwoColumn**: main(fill) + side(360 fixed).
* **TabsBar**: 화면 내부 도메인 분할.
* **Modal / Drawer**: 변경 confirm·dry-run preview·신규 등록.
* **Toast / Alert**: 비동기 결과·undo·health 알림.
* **CommandPalette**: ⌘K — 모든 키·화면·시크릿(이름) 검색.

### 3.4 Domain
* **CronJobRow**: 이름 / 스케줄 / 다음 실행 / 상태 / circuit / 액션.
* **PersonaEditor**: 마크다운 에디터 + token 미터(현재/예산) + diff preview.
* **WebhookGuardCard**: rate-limit·body·concurrency 슬라이더 + 트래픽 시뮬레이션.
* **TraceTimeline**: trace_id 기반 span lane 차트.
* **MemoryClusterMap**: 클러스터 도넛/리스트 + 상위 키워드.

각 컴포넌트의 reusable 정의는 `admin.pen`의 `Components` 페이지에 1:1로 존재해야 하며, 파일 경계를 넘는 일관성은 본 문서와 `admin.pen`의 변수 ID로 보장한다.

---

## 4. Patterns

설계 시 반복되는 패턴 — 새로운 화면을 만들 때 *우선 이 패턴부터 본다.*

### 4.1 Setting Edit Pattern (가장 자주 쓰임)
```
[ Section Header ]      ← 영역명 + 짧은 설명
[ FormRow / InputGroup ]
   - 좌측: 이름·도움말
   - 우측: 입력 + PolicyChip
[ DryRunCard ] (옵션, Hot 변경)
[ Sticky Bar 하단 ]
   - 좌: "변경사항 저장 안 됨" 인디케이터
   - 우: Cancel / Apply (1차 액션)
```
규칙:
* 한 카드에 한 영역. 영역 카드 사이는 `gap 24`.
* Apply 클릭 → DryRunCard 강제 노출(있다면) → Confirm 모달(위험 등급) → Toast(undo).

### 4.2 Secret Display & Rotate
```
[ MaskedSecretRow ]
   key | ••••1234 | [reveal 30s] [copy] [rotate]
```
* `reveal` 클릭 시 카운트다운 30s, 만료 시 자동 마스킹.
* `rotate` 는 `ConfirmGate` 모달 → 새 토큰 생성/입력 → ping 검증 → audit 기록.

### 4.3 Dry-run Preview
```
[ DryRunCard ]
  Before  →  After
  ┌──────────┐  ┌──────────┐
  │ 60 req/m │  │ 30 req/m │
  └──────────┘  └──────────┘
  영향: "최근 1시간 트래픽 중 12건이 새 임계치에서 차단됩니다"
  [Cancel]                   [Apply change]
```

### 4.4 Audit Trail
```
[ AuditEntry ]
  ↻  config.update  llm.providers.claude.model
       claude-sonnet-4-20250514 → claude-opus-4-20250514
       local · 23:30 · trace 01HW… · applied
       [Undo] [View trace]
```
* 시크릿 변경은 before/after 모두 `••••` 마스킹.

### 4.5 Health Surfacing
```
[ TopBar 우측 ]   ●  daemon  ●  webhook  ●  llm  ●  cron
[ Card 우상단 ]   ●  health (해당 카드의 영역)
```
* 변경 적용 직후 5초 동안 해당 dot pulse(motion-slow). 5초 내 green이 안 되면 자동 롤백 제안 모달.

### 4.6 Empty / First-run
```
[ Card centered 480px ]
  Icon (lucide:settings)
  "Cron 작업이 없어요"
  "지금 첫 작업을 만들어 봐요. 가장 흔한 출발은 매일 아침 브리핑이에요."
  [Browse recipes] [Create job]
```

### 4.7 Compact Table
* `$--radius-none`, 행 높이 `44`, 셀 padding `[8,12]`.
* 첫 컬럼은 entity id(굵게), 마지막 컬럼은 액션(IconButton row 정렬 right).
* Sort/필터/검색은 테이블 헤더 컴포넌트로 통합.

### 4.8 Command Palette (⌘K)
* 진입: ⌘K / Ctrl+K.
* 1차 결과: 화면 이동(11개 영역).
* 2차: 설정 키 이름(`webhook.rate_limit`). Enter → 해당 화면으로 이동 + 해당 항목 highlight.
* 시크릿 키는 *이름만* 검색 결과에 노출, 값은 표시하지 않음.

### 4.9 Restart Required
* `Process-restart` 변경은 “지금 재시작 / 다음 데몬 시작 시 적용” 두 옵션을 모달에 노출.
* 재시작 트리거 시: 진행 단계 5개 stepper(저장 → unmount → wait → restart → health) + ETA.

---

## 5. Accessibility

* **WCAG 2.2 AA** 준수. 텍스트 4.5:1, 큰 텍스트·UI 컴포넌트 3:1.
* **포커스 링**: 모든 인터랙티브 요소에 `outline: 2px $--ring; outline-offset: 2px`. 절대 제거 금지.
* **키보드 흐름**: `Tab` 순서는 “사이드바 → TopBar → Main → Sticky Bar”. 모달 진입 시 focus trap.
* **시맨틱 마크업**: 폼은 `<label for>`/`aria-describedby`(Hint, Error). 토글은 `role="switch"` + `aria-checked`.
* **비텍스트 신호 금지**: 색만으로 상태를 표시하지 않는다 (HealthDot은 dot + 라벨, StatusPill은 색 + 텍스트).
* **Reduced motion**: §2.7 참조.
* **스크린 리더**: 토스트는 `role="status"` + `aria-live="polite"`. 위험 confirm 모달은 `role="alertdialog"` + `aria-live="assertive"`.

---

## 6. Voice & Tone

### 6.1 언어
* **본문은 한국어 존댓말** (운영자가 1인이지만 “자기 자신과의 대화”라고 봐도 존중 톤이 안전).
* **버튼·메뉴·라벨**은 짧은 동사형 한국어 (예: “적용”, “회전”, “재시작”, “되돌리기”).
* **로그·시크릿·설정 키**는 영문 그대로 (`webhook.rate_limit`, `keyring:claude_api_key`).

### 6.2 메시지 어조

| 상황 | 톤 | 예시 |
|---|---|---|
| 정보 | 차분/간결 | “변경이 적용됐어요. 5분 안에 되돌릴 수 있습니다.” |
| 성공 | 차분 + 작은 확신 | “Webhook 설정이 적용됐어요.” |
| 경고 | 분명/중립 | “이 변경은 데몬을 재시작합니다. 진행 중인 요청 4건이 끊깁니다.” |
| 위험 | 단호/명령형 회피 | “계속하려면 ‘ROTATE’를 입력해 주세요.” |
| 에러 | 상황 + 다음 행동 | “Telegram API에 연결되지 않았어요. 토큰을 다시 확인하거나 [재시도]를 눌러 주세요.” |

* 금지: 농담·이모지·과한 의인화. (운영 화면은 진지한 환경.)
* 권장: 한 문장 ≤ 50자. 두 번째 문장이 필요하면 그건 보통 모달이 아니라 도움말이다.

### 6.3 마이크로카피 라이브러리(초안)
* `[저장]` / `[변경 적용]` / `[취소]` / `[되돌리기]` / `[삭제]` / `[회전]` / `[재시작]` / `[새로 만들기]` / `[복사]`
* 빈 상태: “아직 X가 없어요. 첫 X를 만들어 봐요.”
* 적용 후: “적용됐어요.”
* 위험 대기: “돌이킬 수 없는 변경입니다. 계속하려면 ‘DELETE’를 입력해 주세요.”

---

## 7. 사용 규칙 / 거버넌스

1. 새로운 화면·기능은 §1의 7개 원칙을 통과해야 한다. 통과 못 하면 디자인 reject.
2. 새로운 컬러·간격이 필요하면 **Primitive 추가 → Semantic 매핑**을 PR로 분리해 머지한 뒤 사용. 임의 hex 사용 금지.
3. 모든 화면은 다크 모드 변형을 같이 디자인한다. (admin.pen에 별도 페이지 없이 토큰 themed value로 처리.)
4. 본 문서가 변경되면 `admin.pen`의 변수/컴포넌트도 동일 PR에서 함께 갱신한다.
5. 후속 구현(프론트/백엔드)은 본 문서를 *시각/상호작용의 단일 진실*로 취급하고, 분기 시 본 문서 PR을 먼저 머지한다.

---

## 8. 부록 A: admin.pen 변수 매핑 (BIZ-38)

`admin.pen` 의 `Variables` 패널은 §2 토큰을 그대로 들고 있으며, 이름은 *prefix 없이* 그대로 키로 사용한다 (예: `--background`, `--font-primary`). 프론트 구현 시 동일 이름의 CSS 변수로 직결될 것을 가정한다 (Tailwind v4 `@theme` 또는 `:root`).

---

## 9. 부록 C: 데이터 패칭 라이브러리 결정 (BIZ-43)

* **채택: SWR(2.x).** Admin UI는 단일 운영자/단일 워크스페이스이므로 글로벌 캐시·낙관적 업데이트 같은 TanStack Query의 고도 기능보다 *불러오고 재검증(stale-while-revalidate)* 모델이 잘 어울리고, 번들 풋프린트(약 4kB gzip)가 작다. 변이는 `swr/mutation`의 `useSWRMutation`으로 키별 분리.
* 표준 진입점: `web/admin/src/lib/api/` (fetchAdmin, useAdminQuery, useAdminMutation, dryRun, useUndo). 영역별 화면은 본 모듈의 export만 사용한다.

---

## 10. 부록 D: a11y · 성능 측정 (BIZ-55)

본 부록은 §5 Accessibility를 *측정 가능*하게 만드는 운영 규약이다. CI에 Lighthouse를 묶어 회귀를 차단한다.

### 10.1 Lighthouse CI

* **워크플로**: `.github/workflows/admin-lighthouse.yml` — `web/admin/**` 변경 PR/푸시에서만 동작. 11개 라우트(대시보드·LLM·페르소나·스킬·Cron·기억·시크릿·채널·로그·감사·시스템) 모두 측정.
* **설정**: `web/admin/lighthouserc.json` — `treosh/lighthouse-ci-action@v12`가 `lhci autorun` 래퍼로 실행하고 임시 공개 스토리지에 보고서를 업로드해 PR 코멘트로 노출한다.
* **CI 게이트(BIZ-55 DoD)**:
  * `categories:accessibility` < 0.95 → **error** (CI 실패)
  * `categories:performance` < 0.85 → warn (코멘트에만 노출)
  * `categories:best-practices` < 0.9 → warn
  * `largest-contentful-paint` > 2500ms → warn (LCP 목표)
  * `cumulative-layout-shift` > 0.1 → warn (CLS 목표)
  * `total-blocking-time` > 200ms → warn (INP의 lab proxy)
* **로컬 실행**: `cd web/admin && npm run build && npm run lhci` — 동일 thresholds로 검증할 수 있다(인터넷 연결 필요, npx로 `@lhci/cli@0.14.x` 가져옴).

### 10.2 키보드 전용 시나리오 (스모크)

키보드만으로 끝까지 도달 가능해야 하는 핵심 흐름. 새 화면이 추가될 때 본 목록에 함께 등록한다.

| 시나리오 | 진입 | 키 | 종료 |
|---|---|---|---|
| 영역 점프 | 어떤 화면에서든 ⌘K | 화면명 입력 → ↓/↑ → Enter | 해당 라우트로 이동 |
| 본문 건너뛰기 | 페이지 로드 직후 Tab 1회 | "본문으로 건너뛰기" 링크 노출 → Enter | `<main>`으로 포커스 이동 |
| 시크릿 회전 | `/secrets` → 항목 행 | Tab으로 [회전] → Enter → 입력 `ROTATE` → Enter | 결과 토스트 + 5분 Undo 슬롯 |
| 페르소나 편집 | `/persona` | Tab으로 파일 탭 → Enter → 본문 편집 → Tab → [적용] | dry-run diff 모달 → Enter로 적용 |
| 크론 잡 추가 | `/cron` → [새 잡] | Tab으로 표현식 → 라벨 → 핸들러 → [저장] | 새 행이 표에 등장 + StatusPill |

* **포커스 가시성**: 전 인터랙티브 요소가 globals.css의 `:focus-visible` 규칙으로 2px 링을 강제. 디자인이 임의로 `outline: none`을 넣으면 PR review에서 reject.
* **Skip link**: `Shell`이 `<a href="#main-content">`를 첫 자식으로 둔다. `sr-only`로 가려졌다가 포커스 시 좌상단에 노출.

### 10.3 VoiceOver(macOS) 검증 시나리오

운영자가 VoiceOver를 켠 상태에서 다음을 점검한다(릴리스 전 1회).

1. **Sidebar 점프**: VO+U(로터 → Landmarks)에서 "주요 영역" / "설정 영역" / "main-content"가 모두 노출되어야 한다.
2. **ConfirmGate 알림**: 시크릿 회전 다이얼로그 진입 시 VoiceOver가 *제목 + 설명*을 발화해야 한다(role="alertdialog" + `aria-labelledby`/`aria-describedby`).
3. **토스트 라이브 리전**: 변경 적용 후 토스트가 자동 발화되어야 한다(`role="status"` + `aria-live="polite"`).
4. **헬스 상태**: Topbar의 4 dot은 색만이 아니라 `title="데몬: 정상"` 형태의 라벨을 가진다 — VO가 라벨을 읽어야 한다(§5 비텍스트 신호 금지).
5. **다크 모드 스왑**: 테마 토글 후 페이지를 재진입했을 때 모든 텍스트가 4.5:1 대비를 유지한다(토큰만 교체되므로 시각 변화에 더해 SR 발화는 영향 없음 — 시각 회귀만 확인).

### 10.4 회귀 차단 흐름

1. PR 작성 → `web/admin/**` 변경 감지 → 워크플로 트리거.
2. `npm ci` → `next build` → `next start --port 3100` → `lhci autorun`.
3. a11y < 95인 라우트가 1건이라도 있으면 CI 실패. 보고서 링크가 PR 코멘트에 자동 첨부.
4. 회귀가 검출되면 본 부록의 시나리오 표를 다시 돌리고, 실패한 항목을 §5의 규칙으로 환원해 수정한다.

> 본 문서는 BIZ-38의 산출물이며, BIZ-39 화면 설계와 후속 구현 이슈의 *유일한 비주얼/인터랙션 진실*로 사용된다.
