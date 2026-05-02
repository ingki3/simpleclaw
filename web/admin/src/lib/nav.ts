/**
 * SimpleClaw Admin — 사이드바 내비게이션 정의.
 *
 * BIZ-40 수용 기준에 따라 11개 라우트가 사이드바에 노출되어야 한다.
 * 각 항목은 App Router의 페이지와 1:1로 대응된다 (`src/app/<href>/page.tsx`).
 * 아이콘은 lucide-react 이름으로 보관해 컴포넌트 측에서 동적으로 매핑한다.
 */

export type NavItem = {
  /** 라우트 경로 — App Router 폴더 이름과 1:1로 대응. */
  href: string;
  /** 한국어 라벨 — DESIGN.md §6 언어 가이드(짧은 명사형). */
  label: string;
  /** lucide-react 아이콘 이름 (kebab → PascalCase는 컴포넌트에서 변환). */
  icon: string;
  /** 접근성 보조 설명 — Tooltip 및 aria-label에 사용. */
  description: string;
};

export const NAV_ITEMS: readonly NavItem[] = [
  {
    href: "/dashboard",
    label: "대시보드",
    icon: "LayoutDashboard",
    description: "데몬 헬스와 최근 활동 요약",
  },
  {
    href: "/llm",
    label: "LLM",
    icon: "Brain",
    description: "프로바이더 라우팅과 폴백",
  },
  {
    href: "/persona",
    label: "페르소나",
    icon: "BookText",
    description: "AGENT.md / USER.md / MEMORY.md",
  },
  {
    href: "/skills",
    label: "스킬",
    icon: "Wrench",
    description: "스킬·MCP 디스커버리와 권한",
  },
  {
    href: "/cron",
    label: "Cron",
    icon: "Clock",
    description: "스케줄러와 하트비트",
  },
  {
    href: "/memory",
    label: "기억",
    icon: "Database",
    description: "대화·드리밍 클러스터",
  },
  {
    href: "/secrets",
    label: "시크릿",
    icon: "KeyRound",
    description: "API 키 회전과 마스킹",
  },
  {
    href: "/channels",
    label: "채널",
    icon: "MessageSquare",
    description: "Telegram, 웹훅, 음성",
  },
  {
    href: "/logs",
    label: "로그",
    icon: "ScrollText",
    description: "구조화 로그와 트레이스",
  },
  {
    href: "/audit",
    label: "감사",
    icon: "ShieldCheck",
    description: "변경 이력과 되돌리기",
  },
  {
    href: "/system",
    label: "시스템",
    icon: "Cog",
    description: "데몬 재시작과 환경",
  },
] as const;
