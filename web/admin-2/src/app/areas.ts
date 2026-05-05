/**
 * Admin 2.0 영역 라우트 SSOT — admin.pen `BpLQy` Navigation Flow Map · DESIGN.md §3.3.
 *
 * Sidebar / TopBar breadcrumb / ⌘K Command Palette / Playwright e2e 가 모두
 * 본 배열만 참조한다. 새 영역 추가는 *반드시* 본 파일 한 곳에서만 하고,
 * S3~S13 sub-issue 들이 콘텐츠를 채워 넣을 때도 path/label 은 변경하지 않는다.
 *
 * 그룹화는 admin.pen 사이드바 시각 spec 에 맞춘다 — 상단 11항목 + 하단 footer.
 * 11개 nav 항목은 단일 그룹으로 묶고, CommandPalette 의 결과 필터에 동일 순서로 노출.
 */
import type { ReactNode } from "react";

export interface AreaDef {
  /** 라우트 path — Next App Router 의 폴더명과 1:1. */
  path: string;
  /** Sidebar / Topbar / CommandPalette 에 노출되는 한국어 라벨. */
  label: string;
  /** ⌘K 검색 보조 키워드 (영문 alias) — DESIGN.md §4.8. */
  keywords: string[];
  /** Sidebar 항목 좌측 아이콘 (현 단계는 단색 글리프 텍스트, S3~S13 가 lucide 로 교체). */
  icon: string;
  /** 영역 한 줄 설명 — Topbar breadcrumb · placeholder 카드의 본문에 노출. */
  description: string;
}

export const AREAS: readonly AreaDef[] = [
  {
    path: "/dashboard",
    label: "대시보드",
    keywords: ["dashboard", "home", "overview"],
    icon: "■",
    description: "데몬·LLM·웹훅·크론 4개 영역의 헬스를 한눈에 본다.",
  },
  {
    path: "/llm-router",
    label: "LLM 라우터",
    keywords: ["llm", "router", "provider", "claude", "openai", "gemini"],
    icon: "↯",
    description: "프로바이더·라우팅 정책·요금/지연을 관리한다.",
  },
  {
    path: "/persona",
    label: "페르소나",
    keywords: ["persona", "agent", "user", "memory", "prompt"],
    icon: "✦",
    description: "AGENT.md / USER.md / MEMORY.md 와 토큰 예산을 편집한다.",
  },
  {
    path: "/skills-recipes",
    label: "스킬 & 레시피",
    keywords: ["skills", "recipes", "tools", "mcp"],
    icon: "▤",
    description: "내장 스킬과 운영자 레시피 카탈로그를 관리한다.",
  },
  {
    path: "/cron",
    label: "크론",
    keywords: ["cron", "schedule", "jobs", "heartbeat"],
    icon: "⏱",
    description: "스케줄 작업의 다음 실행·상태·circuit breaker 를 관리한다.",
  },
  {
    path: "/memory",
    label: "기억",
    keywords: ["memory", "dreaming", "clusters", "active projects"],
    icon: "◌",
    description: "대화·드리밍·Active Projects 를 관리한다.",
  },
  {
    path: "/secrets",
    label: "시크릿",
    keywords: ["secrets", "keyring", "rotate", "api key"],
    icon: "✱",
    description: "API key·토큰의 마스킹·회전·감사 로그를 다룬다.",
  },
  {
    path: "/channels",
    label: "채널",
    keywords: ["channels", "telegram", "webhook", "voice"],
    icon: "✉",
    description: "Telegram·웹훅·STT/TTS 채널 설정을 관리한다.",
  },
  {
    path: "/logging",
    label: "로그",
    keywords: ["logging", "logs", "metrics", "trace"],
    icon: "≡",
    description: "구조화 로그·메트릭·trace timeline 을 본다.",
  },
  {
    path: "/audit",
    label: "감사",
    keywords: ["audit", "history", "undo", "rollback"],
    icon: "⌖",
    description: "변경 이력과 임의 시점 되돌리기를 다룬다.",
  },
  {
    path: "/system",
    label: "시스템",
    keywords: ["system", "backup", "restore", "version", "settings"],
    icon: "⚙",
    description: "데몬 버전·백업·복원·전역 설정을 다룬다.",
  },
] as const;

/** path 로 영역을 조회 (없으면 null). Topbar breadcrumb · 라우터 가드용. */
export function findAreaByPath(path: string): AreaDef | null {
  if (!path) return null;
  // /dashboard/foo 같은 하위 경로도 영역으로 매핑되도록 prefix 매칭.
  const exact = AREAS.find((a) => a.path === path);
  if (exact) return exact;
  return AREAS.find((a) => path.startsWith(`${a.path}/`)) ?? null;
}

/**
 * ⌘K Command Palette 의 1차 결과 — 영역 점프 (DESIGN.md §4.8).
 * 검색어를 label/path/keywords 에 부분일치로 필터링한다.
 */
export function searchAreas(query: string): AreaDef[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...AREAS];
  return AREAS.filter((a) => {
    if (a.label.toLowerCase().includes(q)) return true;
    if (a.path.toLowerCase().includes(q)) return true;
    return a.keywords.some((k) => k.toLowerCase().includes(q));
  });
}

/** 한 곳에서 ReactNode 슬롯이 필요한 곳을 위한 타입 별칭. */
export type AreaIcon = ReactNode;
