/**
 * Dashboard 픽스처 — S3 (BIZ-114) 단계의 mock 데이터.
 *
 * 본 단계는 실제 데몬·LLM·웹훅·크론 API 가 아직 미연결 상태이므로,
 * 운영자가 화면 합성과 시각 회귀를 검증할 수 있도록 정적 fixture 를 제공한다.
 * S13 (System) / S5 (Cron) / S2 (LLM Router) sub-issue 가 실제 데이터 소스로 교체할 때
 * 본 모듈만 삭제/대체하면 되도록, 컴포넌트 prop 에는 fixture 를 직접 노출하지 않고
 * `getDashboardSnapshot()` 한 함수로 묶었다.
 */
import type { HealthTone } from "@/design/molecules/HealthDot";
import type { StatusTone } from "@/design/atoms/StatusPill";
import type { AuditEntryProps } from "@/design/molecules/AuditEntry";

/** Topbar 우측 4-도메인 헬스 — Daemon/LLM/Webhook/Cron 4영역 (DESIGN.md §4.1). */
export interface DomainHealth {
  /** 도메인 키 — testid 와 매핑된다. */
  key: "daemon" | "llm" | "webhook" | "cron";
  label: string;
  tone: HealthTone;
  /** 보조 한 줄 설명 — tooltip / sr-only 로 노출. */
  caption: string;
}

/** 큰 숫자 + delta + 보조 라인 — MetricCard 한 칸에 매핑. */
export interface DashboardMetric {
  key: "messages24h" | "tokens24h" | "alerts" | "uptime";
  label: string;
  value: string;
  delta?: number | string;
  deltaTone?: "positive" | "negative" | "neutral";
  caption?: string;
}

/** Active Projects 패널 한 항목 (admin.pen `XFipm` BIZ-66). */
export interface ActiveProject {
  id: string;
  /** "BIZ-114" 등 이슈 식별자 — 칩 형태로 노출. */
  identifier: string;
  title: string;
  /** "in_progress" | "in_review" | "blocked" 등 상태 라벨. */
  statusLabel: string;
  statusTone: StatusTone;
  /** "Dev Agent" 같은 담당자 — 우측 끝에 표시. */
  owner: string;
  /** 마지막 갱신 시각 사람 친화 표기. */
  updatedAt: string;
  /** 한 줄 요약 — overflow 시 말줄임. */
  excerpt: string;
}

/** 최근 알림/에러 한 줄 — Dashboard 우측 패널. */
export interface DashboardAlert {
  id: string;
  /** 헤드라인 — `webhook · timeout after 30s` 등 source 로 시작. */
  headline: string;
  detail: string;
  tone: StatusTone;
  /** "10분 전" / ISO 등 — 부모가 결정. */
  timestamp: string;
}

export interface DashboardSnapshot {
  domains: readonly DomainHealth[];
  metrics: readonly DashboardMetric[];
  activeProjects: readonly ActiveProject[];
  recentChanges: readonly AuditEntryProps[];
  alerts: readonly DashboardAlert[];
}

/**
 * Dashboard 에 필요한 모든 데이터를 한 번에 반환.
 * 실제 데이터 소스로 교체 시 본 함수 시그니처만 비동기로 바꾸면 된다.
 */
export function getDashboardSnapshot(): DashboardSnapshot {
  return {
    domains: DOMAINS,
    metrics: METRICS,
    activeProjects: ACTIVE_PROJECTS,
    recentChanges: RECENT_CHANGES,
    alerts: ALERTS,
  };
}

const DOMAINS: readonly DomainHealth[] = [
  {
    key: "daemon",
    label: "daemon",
    tone: "green",
    caption: "데몬 정상 · v0.6.1",
  },
  {
    key: "llm",
    label: "llm",
    tone: "green",
    caption: "LLM 라우터 정상 · 3 provider 활성",
  },
  {
    key: "webhook",
    label: "webhook",
    tone: "amber",
    caption: "웹훅 1건 stale-cred — 재인증 필요",
  },
  {
    key: "cron",
    label: "cron",
    tone: "green",
    caption: "크론 12 작업 모두 정상",
  },
];

const METRICS: readonly DashboardMetric[] = [
  {
    key: "messages24h",
    label: "24h 메시지",
    value: "347",
    delta: "+12% vs 어제",
    deltaTone: "positive",
  },
  {
    key: "tokens24h",
    label: "24h 토큰 (in/out)",
    value: "42.1k / 18.6k",
    delta: "-4% vs 어제",
    deltaTone: "negative",
  },
  {
    key: "alerts",
    label: "활성 알람",
    value: "3",
    caption: "webhook stale-cred · llm timeout 외 1",
  },
  {
    key: "uptime",
    label: "가동 시간",
    value: "3d 14h",
    caption: "마지막 재시작 · 2026-05-02 02:14 KST",
  },
];

const ACTIVE_PROJECTS: readonly ActiveProject[] = [
  {
    id: "BIZ-114",
    identifier: "BIZ-114",
    title: "Admin 2.0 — S3 Dashboard 구현",
    statusLabel: "in_progress",
    statusTone: "info",
    owner: "Dev Agent",
    updatedAt: "방금",
    excerpt:
      "시스템 상태 카드 + Active Projects 패널 + 최근 활동 피드 박제 중.",
  },
  {
    id: "BIZ-66",
    identifier: "BIZ-66",
    title: "Memory · Active Projects 패널 — 자동 추출 정책",
    statusLabel: "in_review",
    statusTone: "warning",
    owner: "Biz Agent",
    updatedAt: "2시간 전",
    excerpt:
      "메모리 클러스터에서 Active Projects 자동 추출 룰 v0 — 토큰 예산 검증 대기.",
  },
  {
    id: "BIZ-91",
    identifier: "BIZ-91",
    title: "Pattern frame 박제 — Memory · Skills · Persona",
    statusLabel: "done",
    statusTone: "success",
    owner: "Biz Agent",
    updatedAt: "어제",
    excerpt: "DESIGN.md §3.4 reusable 5종 박제 완료 — admin.pen 1.6 MB.",
  },
  {
    id: "BIZ-63",
    identifier: "BIZ-63",
    title: "Admin Dark Mode 보정 — WCAG AA 검증",
    statusLabel: "done",
    statusTone: "success",
    owner: "Design Agent",
    updatedAt: "3일 전",
    excerpt: "토큰 swap 후 대비비 4.5:1 충족 — 가설 B 채택.",
  },
];

const RECENT_CHANGES: readonly AuditEntryProps[] = [
  {
    actor: "ingki",
    action: "edit",
    target: "memory_note_limit · 16 → 32",
    outcome: "applied",
    timestamp: "10:22 · local",
    traceId: "f1a8c0b34c8f4",
  },
  {
    actor: "ingki",
    action: "rotate",
    target: "telegram_bot_token",
    outcome: "applied",
    timestamp: "10:18 · local · ConfirmGate",
    traceId: "9d20e7b16a1cc",
  },
  {
    actor: "Persona Agent",
    action: "update",
    target: "llm.providers.claude · model → opus-4",
    outcome: "rolled-back",
    timestamp: "09:46 · local",
    traceId: "771b3c11ddee8",
  },
];

const ALERTS: readonly DashboardAlert[] = [
  {
    id: "alert-webhook-stale",
    headline: "webhook · burst from 203.0.113.41/32 in 5s",
    detail: "WebhookGuardCard — burst dampener 발동, 재시도 차단됨.",
    tone: "warning",
    timestamp: "방금 · 09:54 KST",
  },
  {
    id: "alert-llm-timeout",
    headline: "llm.gemini · timeout after 30s on agent fast_loop",
    detail: "fallback → claude:sonnet-4. 5분 내 3회 발생 시 circuit open.",
    tone: "error",
    timestamp: "8분 전",
  },
  {
    id: "alert-cron-daily-briefing",
    headline: "cron · daily-briefing · circuit-break C closed",
    detail: "31분 무응답 후 자가 회복 — 다음 트리거 정상.",
    tone: "info",
    timestamp: "31분 전",
  },
];
