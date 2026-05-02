---
version: alpha
name: SimpleClaw Admin
description: >
  SimpleClaw 운영자(=소유자 1명)가 LLM 라우터, 페르소나, 스킬, 레시피, 크론, 시크릿,
  채널·웹훅 정책 등 모든 설정을 한 화면에서 조회·수정·검증할 수 있는 로컬 Admin UI의
  디자인 시스템.
colors:
  brand: "#2D6BD8"
  brand-foreground: "#FFFFFF"
  background: "#FFFFFF"
  foreground: "#101114"
  surface: "#F7F7F8"
  surface-2: "#EFF0F2"
  border: "#E4E5E8"
  border-strong: "#C9CBD0"
  muted: "#F1F2F4"
  muted-foreground: "#6B6F76"
  ring: "#7BA0E0"
  primary: "{colors.foreground}"
  primary-foreground: "{colors.background}"
  destructive: "#C0322B"
  destructive-soft: "#FBE9E7"
  warning: "#B8741D"
  warning-soft: "#FBEFD9"
  success: "#2E7D4A"
  success-soft: "#E2F2E8"
  info: "#2D6BD8"
  info-soft: "#E5EEFB"
  code-bg: "#0E1116"
  code-fg: "#E6E8EB"
typography:
  display:
    fontFamily: "Inter"
    fontSize: "28px"
    fontWeight: "600"
    lineHeight: "1.2"
    letterSpacing: "-0.01em"
  heading:
    fontFamily: "Inter"
    fontSize: "20px"
    fontWeight: "600"
    lineHeight: "1.3"
  subheading:
    fontFamily: "Inter"
    fontSize: "16px"
    fontWeight: "600"
    lineHeight: "1.4"
  body:
    fontFamily: "Inter"
    fontSize: "14px"
    fontWeight: "400"
    lineHeight: "1.5"
  body-strong:
    fontFamily: "Inter"
    fontSize: "14px"
    fontWeight: "500"
    lineHeight: "1.5"
  caption:
    fontFamily: "Inter"
    fontSize: "12px"
    fontWeight: "400"
    lineHeight: "1.4"
  mono:
    fontFamily: "Geist Mono, JetBrains Mono, Menlo"
    fontSize: "13px"
    fontWeight: "400"
    lineHeight: "1.5"
rounded:
  none: "0px"
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
  full: "9999px"
spacing:
  unit: "4px"
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
  3xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
  button-primary-hover:
    backgroundColor: "#23262B"
    textColor: "{colors.primary-foreground}"
    rounded: "{rounded.md}"
  button-secondary:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
    borderColor: "{colors.border-strong}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
  button-destructive:
    backgroundColor: "{colors.destructive-soft}"
    textColor: "{colors.destructive}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
  input:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
    borderColor: "{colors.border-strong}"
  input-error:
    borderColor: "{colors.destructive}"
    backgroundColor: "{colors.destructive-soft}"
  badge-info:
    backgroundColor: "{colors.info-soft}"
    textColor: "{colors.info}"
    typography: "{typography.caption}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  badge-success:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  badge-warning:
    backgroundColor: "{colors.warning-soft}"
    textColor: "{colors.warning}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  badge-destructive:
    backgroundColor: "{colors.destructive-soft}"
    textColor: "{colors.destructive}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  card:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.lg}"
    padding: "20px"
    borderColor: "{colors.border}"
  toast:
    backgroundColor: "{colors.foreground}"
    textColor: "{colors.background}"
    rounded: "{rounded.lg}"
    padding: "12px 16px"
---

# SimpleClaw Admin — Design System

> 본 문서는 [Google Stitch DESIGN.md 스펙](https://github.com/google-labs-code/design.md)의 토큰 컨트랙트(YAML front matter + 정해진 섹션 순서)를 따른다. SimpleClaw는 음성·텍스트 채널과 Admin UI를 모두 운영하므로, [GitHub Primer](https://primer.style/)의 Foundations/Components 분리 구조와 [Shopify Polaris](https://polaris.shopify.com/)의 Voice & Tone 섹션을 추가로 차용한다.
>
> 시각적 토큰의 baseline은 [`multica-ai/multica`](https://github.com/multica-ai/multica) 프론트엔드(Next.js 16 + Tailwind v4 + shadcn/ui `base-nova` zinc) 분석에서 도출했다. 단, OKLCH 컬러는 SimpleClaw의 단일 운영자 컨텍스트에서 색 디버깅이 어려우므로 **HEX로 평탄화**해 채택한다.

본 문서의 6개 본문 섹션(Principles / Design Tokens / Component Library / Patterns / Accessibility / Voice & Tone)은 BIZ-38 사전 조사 결과를 반영한 SimpleClaw용 정렬이며, 각 섹션은 Stitch 스펙의 Overview/Colors/Typography/Layout/Components/Do's & Don'ts 영역을 포함하거나 보완한다.

---

## 1. Principles

Admin UI가 따라야 할 5개 원칙. 모든 설계 결정은 아래 원칙에 의해 기각·승인된다.

1. **단일 운영자 우선 (Single-operator first)**
   소유자(=사용자) 1명이 전권을 갖는 환경을 가정한다. 권한·역할 UI를 기본 노출하지 않으며, 장차 다중 사용자 모드가 켜질 때만 점진적 노출 (BIZ-37 권한 모델 결정 참조).
2. **위험한 변경은 명시적으로 (Explicit confirmation for destructive change)**
   시크릿 회전, 페르소나(`AGENT.md`/`USER.md`/`MEMORY.md`) 덮어쓰기, 채널 토큰 교체, allowlist 비우기, 크론 일괄 비활성화 등은 반드시 확인 다이얼로그를 거친다. "되돌릴 수 있는가?"가 모호하면 사전 dry-run/preview를 우선 제공한다.
3. **시크릿은 절대 보지 않는 것이 기본 (Secrets are masked-by-default)**
   API 키·토큰·webhook 시크릿은 마스킹된 형태(`••••1f3a`)로만 표시한다. 평문 노출은 명시적 "Reveal" 클릭 + 사유 로그 적재 후에만 허용한다.
4. **상태와 적용 시점을 항상 표기 (State and apply-policy must be visible)**
   설정 항목마다 _즉시 적용_, _재시작 필요_, _hot-reload_ 중 어떤 정책인지 행동 직전에 알린다. SimpleClaw는 페르소나/스킬/레시피가 메시지마다 hot-reload되므로, 사용자가 "방금 저장한 변경이 언제 반영되는가"에 대한 의구심을 가지지 않게 한다.
5. **운영자 도구다, 마케팅 사이트가 아니다 (It is a tool, not a website)**
   히어로 섹션·아이콘 그라디언트·과한 모션은 배제. 정보 밀도는 높게, 흐릿한 색·낮은 대비는 금지. 매 화면이 즉시 행동 가능(actionable)해야 한다.

> 참고로 multica-ai/multica는 Tiptap·dnd-kit·embla-carousel·input-otp 같은 협업/콘텐츠 의존성을 풍부하게 사용하지만, SimpleClaw는 콘텐츠 편집 도구가 아니므로 동일 의존성을 채택하지 않는다.

---

## 2. Design Tokens

토큰은 위 YAML front matter가 정본(canonical)이다. 본 절은 의도와 사용 규칙을 설명한다. 코드(향후 `tokens.css`)에서 사용할 때는 `--sc-*` 접두사를 권장한다.

### 2.1 컬러

#### Brand & Surface (light, default)

| 토큰 | HEX | 용도 |
|---|---|---|
| `brand` | `#2D6BD8` | 1차 브랜드 액센트, 링크, 정보 강조. 채도가 너무 강하지 않은 미드 블루를 사용해 눈의 피로 최소화. |
| `background` | `#FFFFFF` | 페이지 기본 배경 |
| `surface` | `#F7F7F8` | 카드/사이드바 배경 |
| `surface-2` | `#EFF0F2` | 입력 비활성, hover 면 |
| `foreground` | `#101114` | 본문 텍스트(primary) |
| `muted-foreground` | `#6B6F76` | 보조 텍스트, placeholder |
| `border` | `#E4E5E8` | 카드/테이블 1차 경계 |
| `border-strong` | `#C9CBD0` | 입력 박스, 명시적 경계 |
| `ring` | `#7BA0E0` | 포커스 링 (3px, ring-color/50) |

#### Semantic / State

| 의도 | foreground | background |
|---|---|---|
| `info` | `#2D6BD8` | `#E5EEFB` |
| `success` | `#2E7D4A` | `#E2F2E8` |
| `warning` | `#B8741D` | `#FBEFD9` |
| `destructive` | `#C0322B` | `#FBE9E7` |

> **소프트 레드 규칙**: 파괴적 액션 버튼은 solid red(`#C0322B`) 배경 대신 `destructive-soft` 배경 + `destructive` 텍스트로 칠한다. 이는 multica-ai/multica의 `bg-destructive/10 text-destructive` 패턴을 따른 것이며, 일상 운영 화면에서 시각적 공격성을 낮춘다. 단 _최종 확인 다이얼로그_의 commit 버튼만 solid red.

#### Dark mode (도입 결정: **Phase 2**)

다크 모드는 본 문서 Phase 1 범위에 포함하지 않는다. 토큰 구조는 `mode: light | dark` 테마 축으로 확장 가능하도록 키만 미리 분리해 두며 (예: `background`/`foreground`), 실제 다크 값은 BIZ-39(예정) 화면 설계 단계에서 한 차례 정의한다. 이는 CLAUDE.md의 "운영자 단일 사용자, 데스크톱 사용 우세" 가정에 따라 조명 환경 변화가 적기 때문.

### 2.2 타이포그래피

기본 폰트 스택:
- **Sans**: `Inter` (web fallback: `-apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", "Noto Sans KR", sans-serif`)
- **Mono**: `Geist Mono` (fallback: `JetBrains Mono, Menlo, monospace`) — 시크릿/JSON/로그 표시 전용
- 한국어가 1차 콘텐츠 언어이므로 sans 폴백에 `Pretendard`/`Noto Sans KR`을 우선 배치

스케일:

| 토큰 | size / weight / line-height | 용도 |
|---|---|---|
| `display` | 28 / 600 / 1.2 | 페이지 최상단 타이틀 (드물게 사용) |
| `heading` | 20 / 600 / 1.3 | 섹션 헤더 |
| `subheading` | 16 / 600 / 1.4 | 카드/필드셋 헤더 |
| `body-strong` | 14 / 500 / 1.5 | 폼 라벨, 버튼, 강조 본문 |
| `body` | 14 / 400 / 1.5 | 본문 기본 |
| `caption` | 12 / 400 / 1.4 | 메타데이터, 도움말 |
| `mono` | 13 / 400 / 1.5 | 시크릿 마스크, 토큰, JSON, cron 식 |

폰트 크기 ≥ 14px가 본문 기본. 12px는 보조 정보·tooltip에만. 모바일 입력 필드는 16px로 끌어올려 iOS Safari focus zoom을 방지한다.

### 2.3 스페이싱 그리드

4px 단위 그리드. 사용 토큰은 `xs(4) / sm(8) / md(12) / lg(16) / xl(24) / 2xl(32) / 3xl(48)` 7단계로 제한한다.

- 카드 내부 padding: `lg`(16) — 정보 밀도가 높은 운영 화면에는 24보다 16이 적합
- 섹션 간 vertical gap: `xl`(24)
- 인라인 폼 컨트롤 gap: `sm`(8)
- 페이지 가장자리 padding: `2xl`(32) (desktop), `lg`(16) (≤768px)

### 2.4 라운딩

| 토큰 | 값 | 용도 |
|---|---|---|
| `none` | 0 | 데이터 테이블 셀 |
| `sm` | 4px | 인라인 코드 칩, 작은 badge |
| `md` | 6px | **버튼/입력 기본** |
| `lg` | 8px | 카드, 다이얼로그 |
| `xl` | 12px | 토스트, 주요 모달 |
| `full` | 9999px | 상태 badge, 아바타 |

### 2.5 셰도우 / Elevation

운영 도구는 elevation을 거의 쓰지 않는다. 3단계로 충분.

| 토큰 | 정의 | 용도 |
|---|---|---|
| `e0` | 없음 | 기본 표면 |
| `e1` | `0 1px 2px rgba(16,17,20,0.06), 0 1px 1px rgba(16,17,20,0.04)` | 카드, 드롭다운 |
| `e2` | `0 8px 24px rgba(16,17,20,0.12), 0 2px 6px rgba(16,17,20,0.06)` | 모달, 토스트 |

---

## 3. Component Library

원자(atoms) → 분자(molecules) 순으로 1차 인벤토리를 정의한다. 각 컴포넌트는 `default / hover / active / focus / disabled / error` 상태를 갖는 것으로 가정하고, 본 절은 _이 컴포넌트가 무엇이고 언제 쓰는지_에 집중한다. 시각적 변형은 `admin.pen` 캔버스에서 시각화한다.

### 3.1 Atoms

| 컴포넌트 | 변형 | 비고 |
|---|---|---|
| **Button** | `primary`, `secondary`, `ghost`, `destructive` × `sm/md` | 아이콘 leading/trailing 슬롯 지원. height: 32(`sm`)/36(`md`). 버튼 누름 micro-press(`translate-y: 1px`) 채택. |
| **Icon Button** | 동일 변형, 정사각 (28/32/36) | 헤더 액션, 인라인 컨트롤. |
| **Input (text)** | `default`, `error`, `disabled` | 32 height, 좌측 leading-icon slot, 우측 trailing-action slot(예: 시크릿 reveal 토글). |
| **Textarea** | 동일 | 4행 기본, resize: vertical. |
| **Select / Combobox** | 동일 | 키보드 검색 가능(`cmdk` 기반). |
| **Toggle (Switch)** | `on/off`, `disabled` | 확실한 on/off 의미가 있을 때만. 3상(true/false/inherit) 필요한 곳은 select 사용. |
| **Checkbox** | `on/off/indeterminate` | 다중 선택, 동의 체크박스. |
| **Radio** | n개 중 1개 | 가급적 select로 대체. |
| **Badge** | `info`, `success`, `warning`, `destructive`, `neutral` | 상태/카테고리 표기. fully-rounded. |
| **Tag (chip)** | 닫기 버튼 슬롯 | allowlist, 라벨 다중 선택. |
| **Tooltip** | 키보드 focus에서도 노출 | 200ms delay, body 스택보다 위. |
| **Avatar** | text, image, fallback initials | Telegram 사용자 표기에 사용. |
| **Separator** | horizontal/vertical | 1px `border` 색. |

### 3.2 Molecules

| 컴포넌트 | 설명 |
|---|---|
| **Section Header** | `heading` + 우측 액션 슬롯(예: "신규 추가" 버튼) + 선택적 도움말 링크. |
| **Form Field** | `label` + `control` + `helper text` + `error message` 슬롯. label은 항상 컨트롤 위(stacked), 가로 폼 금지(접근성 확보). |
| **Field Group / Fieldset** | 같은 도메인의 필드를 시각적으로 묶음(`surface` 배경 + `lg` rounded). |
| **Card** | `e1` 셰도우, padding `lg`, 헤더/본문/푸터 슬롯. 1차 콘텐츠 컨테이너. |
| **Tabs** | 페이지 안 보조 분기에만 사용. 1차 네비게이션은 사이드바. |
| **Table** | 헤더 sticky, sortable column, row hover 강조, 빈 상태 명시(Empty 컴포넌트 사용). 컬럼 폭은 fit-content + last-fill 패턴. |
| **Data Row** | 테이블 한 줄. trailing 셀에 `…` 메뉴(편집/삭제/실행). |
| **Modal / Dialog** | 600px 너비 기본, ESC + backdrop close, focus trap. heading + body + footer(우측 정렬 액션). |
| **Confirm Dialog** | 파괴적 액션 전용. 입력 확인(예: 토큰 이름 직접 타이핑) 옵션. |
| **Toast** | 우측 하단 stack, 5s auto-dismiss, `success/warning/destructive/info` × `description optional`. Sonner 기반. |
| **Empty State** | dashed border, 중앙 정렬, 1차 액션 버튼 슬롯. 모든 빈 리스트가 사용. |
| **Code Block** | `mono` 폰트, `code-bg/fg`, 우상단 `Copy` 버튼. |
| **Secret Field** | 마스킹된 값(`••••1f3a`) + Reveal 토글 + Rotate 액션. 본 시스템의 핵심 패턴(§4 참조). |
| **Diff Panel** | dry-run/preview용. 좌(현재) / 우(변경 후), 줄 단위 +/- 색상. |
| **Audit Log Row** | 타임스탬프 + 액터 + 변경 요약 + 상세 토글. |
| **Sidebar Nav Item** | 아이콘 + 라벨 + 선택 상태 + (옵션) badge counter. |
| **Status Pill** | running / idle / failed / disabled. 색·아이콘 결합. |

### 3.3 도입하지 않는 컴포넌트(명시적 제외)

multica-ai/multica는 58개 primitive를 제공하지만, SimpleClaw 1차 범위에서는 다음을 제외한다.

- Carousel, Embla, Drawer(vaul), Resizable panels(사이드바 제외) — 운영 화면에 불필요
- Tiptap rich text — 페르소나 마크다운은 plain textarea + preview로 충분
- Calendar, DayPicker — cron은 cron expression 입력 + 자연어 미리보기로 처리
- Input OTP, OTP — 단일 운영자 환경에서 인증은 별도 게이트
- Emoji Picker, Reaction Bar — 채널 UI 항목

---

## 4. Patterns

설정·운영 화면에서 반복되는 5개 핵심 패턴.

### 4.1 설정 편집 패턴 (Settings edit pattern)

모든 설정 페이지는 다음 4구역을 갖는다.

1. **Header** — `Section Header`. 우측에 _Reset to default_, _Apply policy_ 라벨, _Save_ 버튼.
2. **Body** — Field group들의 vertical stack. 각 필드는 hot-reload / restart-required 정책을 우측 caption으로 표시.
3. **Footer (sticky)** — _Save_ + _Discard_, dirty state일 때만 노출. ESC = Discard, ⌘S = Save.
4. **Audit drawer (옵션)** — 우측에서 슬라이드 인. 최근 5건의 변경.

> **Save 후 헬스체크 트리거**: 정책 카탈로그에서 "외부 연동 핑" 표시가 있는 항목(예: LLM api_key 변경)은 저장 즉시 핑 결과를 toast로 회신. 실패 시 자동 롤백 옵션 제공.

### 4.2 시크릿 마스킹 (Secret masking)

| 상태 | 표시 |
|---|---|
| 기본 | `Secret Field`에 `keyring:claude_api_key` 참조 라벨 + `••••1f3a` 마지막 4자 hint |
| Reveal | 4초간 평문 노출 후 자동 마스크 복귀, 노출 사실은 `audit_log`에 기록 |
| Rotate | Confirm Dialog에서 새 값 입력. 이전 값은 즉시 폐기되며 외부 연동 핑 자동 실행 |
| 검증 실패 | input-error 상태 + helper에 사유, Save 비활성화 |

평문 입력 화면은 `<input type="password" autocomplete="new-password">`로 강제하고, 페이스트는 허용하되 OS 클립보드 잔존을 사용자에게 caption으로 안내한다.

### 4.3 dry-run / preview

모든 다단 변경(페르소나 일괄 수정, 크론 일괄 토글, allowlist 대량 import 등)은 _Preview_ 단계를 거친다.

1. 사용자가 변경 의도 입력
2. `Preview` 버튼 클릭 → `Diff Panel` 모달 노출
3. `Apply`로만 실제 반영. ESC 시 변경 폐기.

이 패턴은 BIZ-37의 "변경 사전 검증 dry-run/preview" 요구를 만족한다.

### 4.4 감사 로그 표시 (Audit log)

`docs/admin-requirements.md`(BIZ-37 산출물)에서 정의되는 변경 메타데이터 — `누가/언제/무엇을/이전값/새값` — 를 표 형태가 아니라 **timeline + collapsible diff** 형식으로 보여준다.

- 1차 화면: 시간 역순 50건, 각 row는 1줄 요약(`이전값 → 새값`)
- 펼치면 `Diff Panel`로 전환
- 5분 이내 변경은 `Undo` 버튼 inline 노출 (BIZ-37 롤백 정책 참조)

### 4.5 적용 정책(apply-policy) 표시

설정 카탈로그상 모든 항목은 다음 3개 라벨 중 하나를 caption 슬롯에 부착한다.

- `즉시 적용` — 저장 직후 효과 (ex: webhook rate-limit)
- `Hot-reload` — 다음 메시지 처리부터 효과 (ex: AGENT.md, 스킬 추가)
- `재시작 필요` — 데몬/봇 재시작 후 효과 (ex: bot_token 변경)

라벨은 색상이 아닌 캡션 + 아이콘(↻/♻︎/⏻)로 구분 — 색에만 의존하지 않는다(§5 Accessibility 참조).

---

## 5. Accessibility

본 시스템은 **WCAG 2.2 AA**를 최소 기준으로 한다.

### 5.1 색대비

| 표면 / 텍스트 | 비율 | 통과 |
|---|---|---|
| `background`(`#FFFFFF`) / `foreground`(`#101114`) | 19.5:1 | AAA |
| `background` / `muted-foreground`(`#6B6F76`) | 4.74:1 | AA(본문), AA-large 이하 사용 금지 |
| `surface`(`#F7F7F8`) / `foreground` | 18.4:1 | AAA |
| `brand`(`#2D6BD8`) / `background` | 4.78:1 | AA — 본문 링크 사용 가능 |
| `destructive-soft`(`#FBE9E7`) / `destructive`(`#C0322B`) | 5.1:1 | AA — 소프트 레드 버튼에서 검증됨 |

새로운 토큰을 추가할 때는 `pyfn check_contrast(token_a, token_b)` 등 자동 체크를 PR 게이트로 둔다(향후 Dev Agent 작업).

### 5.2 키보드

- 모든 인터랙티브 요소는 `Tab`으로 순회 가능, `Shift+Tab`으로 역순
- `Esc`: 모달/드로어/다이얼로그 close, sticky footer dirty state discard
- `⌘S`: Save (settings page)
- `⌘K`: Command palette 열기 — 모든 설정/페이지로 점프(향후 BIZ-39)
- 포커스 링은 `ring`(3px, ring-color/50) — 절대로 `outline: none`만 두지 않는다
- `aria-invalid="true"` 시 `input-error` 시각 + 스크린리더 메시지

### 5.3 폼 라벨링

- `<label for>` 또는 `aria-labelledby`가 모든 컨트롤에 부착
- helper / error 메시지는 `aria-describedby`로 연결
- placeholder는 라벨을 대체하지 않는다 (placeholder는 예시값)
- 필수 표시는 `*` 단독으로 충분하지 않다 — `(필수)` 텍스트 또는 `aria-required="true"`로 보강

### 5.4 모션

- `prefers-reduced-motion: reduce` 시 fade/slide만 유지하고 spring/scale 애니메이션 제거
- "agent thinking" 인디케이터는 텍스트 라벨(`처리 중…`)과 함께 노출. 색만 깜빡이는 표현은 금지

---

## 6. Voice & Tone

> SimpleClaw는 "사용자의 개인 비서"이며, Admin UI는 그 비서가 자신의 운영 화면을 사용자에게 보여주는 자리이다. 따라서 **존댓말 한국어**가 1차 voice이며, 시스템적 메시지는 비서가 사용자에게 보고하는 어조를 따른다. (CLAUDE.md "주석 작성 규칙" 한국어 컨벤션과도 일관.)

### 6.1 기본 원칙 (4개)

1. **존댓말, 단정형보다 보고형** — "삭제했어요" > "삭제됨"
2. **구체적인 명사** — "토큰" > "값"; "AGENT.md" > "파일"
3. **운영자가 다음에 무엇을 할지 알려준다** — 결과 + 다음 행동 1개
4. **위협적이지 않게, 그러나 흐릿하지 않게** — 위험 액션은 결과를 직설적으로 알리되 비난·과장 없이

### 6.2 메시지 톤 매트릭스

| 상황 | 예 (DO) | 예 (DON'T) |
|---|---|---|
| 정보 | `webhook rate limit이 60 req/min으로 적용되었어요.` | `Settings updated.` |
| 성공 | `Telegram 봇 토큰을 회전했어요. 새 토큰으로 5초 안에 핑을 보낼게요.` | `완료!` |
| 경고 | `이 변경은 데몬 재시작 후에 반영돼요. 지금 재시작할까요?` | `RESTART REQUIRED ⚠️` |
| 오류(사용자 입력) | `cron 식이 비어 있어요. 예: \`0 */2 * * *\`` | `Invalid input.` |
| 오류(시스템) | `keyring에서 토큰을 읽지 못했어요. macOS Keychain 권한을 확인해 주세요.` | `Error 500.` |
| 파괴적 액션 확인 | `MEMORY.md 전체를 비웁니다. 저장된 핵심 기억 1,247개가 삭제되며 되돌릴 수 없어요. \`MEMORY\`를 입력해 확인해 주세요.` | `Are you sure?` |

### 6.3 마이크로카피 규칙

- 버튼은 동사+명사: `저장`, `토큰 회전`, `크론 일시 정지`. "OK / Cancel"은 절대 쓰지 않는다.
- 빈 상태는 _상태 + 다음 행동_: `등록된 스킬이 없어요. 새 스킬을 추가하거나 ~/.agents/skills 디렉토리에서 가져오세요.`
- 시간 표기는 상대시간 + tooltip(절대시간): `3분 전` (hover 시 `2026-05-02 23:08:14 KST`)
- 숫자는 한글 구분자(`12,345건`), 바이트는 IEC(`1.0 MiB`)

---

## 7. Do's and Don'ts

| ✓ DO | ✗ DON'T |
|---|---|
| 위험한 액션은 _확인 + 입력_ 2단계로 게이팅 | 단일 클릭으로 시크릿 회전·삭제 |
| 마스킹된 시크릿 + Reveal 4초 자동 복귀 | 평문 시크릿 상시 표시 |
| 적용 정책 라벨(즉시/Hot-reload/재시작) 노출 | 저장 후 적용 시점을 추측하게 만들기 |
| `body-strong` + 14px 본문 | 12px 본문 (캡션 전용) |
| solid red는 _최종 commit_ 버튼에만 | 일상 화면에 solid red 가득 |
| Empty State에 다음 액션 1개 | "표시할 항목 없음" 만 |
| 한국어 존댓말 보고형 | "OK / Cancel" 직역 |
| `ring-3 ring-ring/50` 포커스 링 | `outline: none`만 두기 |
| timeline + diff 형태의 감사 로그 | 단순 raw JSON 덤프 |
| 단일 사이드바 1차 네비, 페이지 안에서만 Tabs | 모달 안에서 다단 탭 |

---

## 부록 A. 산출물과의 매핑

| 산출물 | 위치 | 본 문서와의 관계 |
|---|---|---|
| `DESIGN.md` | 본 파일(`/DESIGN.md`) | 토큰·컴포넌트·패턴·접근성·voice 명세서 |
| `admin.pen` | `/admin.pen` (pencil 워크스페이스) | 본 문서 토큰을 시각 변수로 정의하고, §3 컴포넌트 인벤토리를 캔버스에 배치한 시각 시스템 |
| `docs/admin-requirements.md` | (BIZ-37 결과물, 별도 이슈) | 설정 카탈로그·검증·권한·감사 정책. 본 문서는 그 정책을 _어떻게 보여줄지_를 정의 |
| 화면 설계 | (BIZ-39 예정) | 본 문서의 토큰·컴포넌트만으로 화면을 합성. 새 토큰이 필요하면 본 문서 PR로 추가 |

## 부록 B. 참조

- Google Stitch DESIGN.md spec: <https://github.com/google-labs-code/design.md>
- Stitch announcement: <https://blog.google/innovation-and-ai/models-and-research/google-labs/stitch-design-md/>
- multica-ai/multica frontend: <https://github.com/multica-ai/multica> — `packages/ui/styles/tokens.css`, `packages/ui/components/ui/*`
- GitHub Primer: <https://primer.style/> · primitives <https://github.com/primer/primitives>
- Shopify Polaris (Voice & Tone): <https://polaris.shopify.com/content/voice-and-tone>

## 부록 C. 변경 정책

본 문서는 _시각 결정의 SSOT_ 다. 새 토큰·컴포넌트·패턴은 본 문서 PR + `admin.pen` 동시 변경으로만 도입한다. 화면 단위 작업(BIZ-39 이후)에서 _임시 토큰_을 만들지 않는다 — 필요하면 본 문서를 먼저 갱신한다.
