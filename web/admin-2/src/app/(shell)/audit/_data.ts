/**
 * Audit 픽스처 — S12 (BIZ-123) 단계의 mock 데이터.
 *
 * admin.pen `Auu2Y` (Audit Shell) · `nHHuf` (Undo Confirm modal, BIZ-109 P1)
 * 시각 spec 에 1:1 매핑한다. 본 단계는 audit 데몬 API 가 아직 미연결이므로
 * 정적 fixture 만 노출한다. 후속 sub-issue (데몬 통합 단계) 가 본 모듈만
 * 비동기로 교체하면 호출부 변경이 없도록, 컴포넌트는 fixture 를 직접
 * import 하지 않고 `getAuditSnapshot()` 한 함수만 호출한다.
 */

/**
 * 감사 로그 한 건의 액션 분류 — admin.pen `Auu2Y` 의 Action 컬럼 SSOT.
 *
 * 헤더 우측 "outcome 실패만" 토글과 직교하는 area 차원 — `Click Paths` 카드의
 * "area 칩 → area 차원 필터" 가 실제로는 본 area 코드를 묶음 매핑한다.
 */
export type AuditAction =
  | "config.update"
  | "secret.rotate"
  | "persona.publish"
  | "skill.install"
  | "skill.uninstall"
  | "channel.update"
  | "cron.toggle"
  | "memory.delete"
  | "daemon.restart";

/**
 * 영역 분류 — Sidebar 11개 영역 중 audit 가 추적하는 7개 운영 차원.
 * 헤더 좌측 "area" 드롭다운의 SSOT.
 */
export type AuditArea =
  | "llm-router"
  | "persona"
  | "secrets"
  | "skills-recipes"
  | "channels"
  | "cron"
  | "memory"
  | "system";

/** 감사 로그 결과 — Undo 가능 여부 판단의 SSOT. */
export type AuditOutcome = "applied" | "rolled-back" | "failed" | "pending";

/** 시간 범위 필터 — 헤더 드롭다운 옵션의 SSOT. */
export type AuditTimeRange = "24h" | "7d" | "30d" | "90d" | "all";

/**
 * 감사 로그 한 건 — 표 한 행과 1:1.
 *
 * `before`/`after` 가 모두 있으면 변경(diff) 으로 시각화하고, after 만 있으면
 * 신규 (예: secret.rotate), before 만 있으면 삭제 (예: memory.delete) 로 본다.
 * Undo 는 `outcome === "applied"` 인 경우에만 활성 — rolled-back/failed/pending 행은
 * 우측 액션 슬롯이 비활성 상태로 노출된다.
 */
export interface AuditEntry {
  /** 영구 키 — slug. */
  id: string;
  /** ISO timestamp. 화면은 yyyy-MM-dd HH:mm:ss 까지 노출. */
  timestamp: string;
  /** 변경을 일으킨 주체 — 사람(operator), DesignAgent, system 등. */
  actor: string;
  /** 액션 코드 — Action 컬럼의 SSOT. */
  action: AuditAction;
  /** 영역 — `Auu2Y` 의 area 칩 필터 SSOT. */
  area: AuditArea;
  /** 사람이 읽는 대상 경로 — 예: `llm.providers.claude/timeout_ms`. */
  target: string;
  /** 변경된 필드 명 — Undo 모달 상단 메타 행의 SSOT. */
  field?: string;
  /** 변경 전 값 — 사람이 읽는 짧은 문자열. */
  before?: string;
  /** 변경 후 값. */
  after?: string;
  /** 결과 — Undo 활성 판단 + outcome 필터 SSOT. */
  outcome: AuditOutcome;
  /** trace 연결 — 현 단계는 표시만, 클릭 시 동작은 후속 sub-issue 가 wiring. */
  traceId?: string;
}

export interface AuditSnapshot {
  entries: readonly AuditEntry[];
}

/** 페이지 헤더 시간 범위 드롭다운의 옵션 SSOT. */
export const TIME_RANGE_OPTIONS: ReadonlyArray<{
  value: AuditTimeRange;
  label: string;
}> = [
  { value: "24h", label: "최근 24시간" },
  { value: "7d", label: "최근 7일" },
  { value: "30d", label: "최근 30일" },
  { value: "90d", label: "최근 90일" },
  { value: "all", label: "전체 기간" },
];

/** 페이지 헤더 area 드롭다운의 옵션 SSOT — "all" 포함. */
export const AREA_OPTIONS: ReadonlyArray<{
  value: AuditArea | "all";
  label: string;
}> = [
  { value: "all", label: "전체 영역" },
  { value: "llm-router", label: "LLM 라우터" },
  { value: "persona", label: "페르소나" },
  { value: "secrets", label: "시크릿" },
  { value: "skills-recipes", label: "스킬 & 레시피" },
  { value: "channels", label: "채널" },
  { value: "cron", label: "크론" },
  { value: "memory", label: "기억" },
  { value: "system", label: "시스템" },
];

/** 페이지 헤더 action 드롭다운의 옵션 SSOT — "all" 포함. */
export const ACTION_OPTIONS: ReadonlyArray<{
  value: AuditAction | "all";
  label: string;
}> = [
  { value: "all", label: "전체 액션" },
  { value: "config.update", label: "config.update" },
  { value: "secret.rotate", label: "secret.rotate" },
  { value: "persona.publish", label: "persona.publish" },
  { value: "skill.install", label: "skill.install" },
  { value: "skill.uninstall", label: "skill.uninstall" },
  { value: "channel.update", label: "channel.update" },
  { value: "cron.toggle", label: "cron.toggle" },
  { value: "memory.delete", label: "memory.delete" },
  { value: "daemon.restart", label: "daemon.restart" },
];

/** 시간 범위 → 분 단위 환산. 필터 helper 가 사용. */
export function timeRangeMinutes(range: AuditTimeRange): number {
  switch (range) {
    case "24h":
      return 60 * 24;
    case "7d":
      return 60 * 24 * 7;
    case "30d":
      return 60 * 24 * 30;
    case "90d":
      return 60 * 24 * 90;
    case "all":
      // "전체" 는 사실상 무한 — 충분히 큰 수로 클램프.
      return 60 * 24 * 365 * 10;
  }
}

/** Audit 화면이 그릴 모든 데이터를 한 번에 반환. */
export function getAuditSnapshot(): AuditSnapshot {
  return { entries: ENTRIES };
}

/** entries 의 distinct actor 목록 — 헤더 actor 드롭다운 SSOT. */
export function listActors(entries: readonly AuditEntry[]): string[] {
  const set = new Set<string>();
  for (const e of entries) set.add(e.actor);
  return Array.from(set).sort();
}

/** id 로 AuditEntry 단건 조회 — Undo Confirm modal 오픈 시 사용. */
export function findEntryById(
  entries: readonly AuditEntry[],
  id: string,
): AuditEntry | null {
  return entries.find((e) => e.id === id) ?? null;
}

/** Undo 가능 여부 — Undo 버튼 활성/비활성 판단 SSOT. */
export function canUndo(entry: AuditEntry): boolean {
  return entry.outcome === "applied";
}

export interface AuditFilter {
  /** 자유 검색 — actor / target / action / before / after 부분 일치. */
  query: string;
  area: AuditArea | "all";
  action: AuditAction | "all";
  actor: string | "all";
  range: AuditTimeRange;
  /** "outcome 실패만" 토글 — true 면 outcome === "failed" 만 노출. */
  failedOnly: boolean;
  /**
   * "지금" 의 기준 시각. 테스트가 결정적으로 동작하도록 외부에서 주입 가능.
   * 미지정 시 마지막 entry 의 timestamp 를 기준으로 — fixture 가 고정 시각이라
   * `Date.now()` 에 의존하면 테스트가 시간 경과에 따라 깨지기 때문.
   */
  now?: number;
}

/**
 * 6개 필터를 한 번에 적용 — page 와 단위 테스트가 공유.
 *
 * 정렬은 timestamp 내림차순 (최신 먼저) — 표의 시각적 SSOT.
 */
export function applyAuditFilter(
  entries: readonly AuditEntry[],
  filter: AuditFilter,
): AuditEntry[] {
  const q = filter.query.trim().toLowerCase();
  const nowMs =
    filter.now ??
    (entries.length > 0
      ? Math.max(...entries.map((e) => Date.parse(e.timestamp)))
      : Date.now());
  const cutoff = nowMs - timeRangeMinutes(filter.range) * 60_000;
  const filtered = entries.filter((e) => {
    if (filter.area !== "all" && e.area !== filter.area) return false;
    if (filter.action !== "all" && e.action !== filter.action) return false;
    if (filter.actor !== "all" && e.actor !== filter.actor) return false;
    if (filter.failedOnly && e.outcome !== "failed") return false;
    if (Date.parse(e.timestamp) < cutoff) return false;
    if (q.length > 0) {
      const haystack = [
        e.actor,
        e.target,
        e.action,
        e.field ?? "",
        e.before ?? "",
        e.after ?? "",
      ]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
  filtered.sort(
    (a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp),
  );
  return filtered;
}

// ────────────────────────────────────────────────────────────────────────────
// Fixtures — 14 entries.
// admin.pen `Auu2Y` 시각 spec 의 표 데이터를 실제 운영 시나리오로 확장.
// 시간 분포는 24h/7d/30d 필터가 모두 결과를 갖도록 의도적으로 흩어 놓는다.
// outcome 분포 — applied 9, rolled-back 2, failed 2, pending 1.
// ────────────────────────────────────────────────────────────────────────────

const ENTRIES: readonly AuditEntry[] = [
  {
    id: "audit-1",
    timestamp: "2026-05-05T14:32:31.000Z",
    actor: "ingki3",
    action: "config.update",
    area: "llm-router",
    target: "llm.providers.claude/timeout_ms",
    field: "timeout_ms",
    before: "30000",
    after: "60000",
    outcome: "applied",
    traceId: "trace-cfg-001",
  },
  {
    id: "audit-2",
    timestamp: "2026-05-05T13:08:42.000Z",
    actor: "ingki3",
    action: "secret.rotate",
    area: "secrets",
    target: "secrets/openai_key",
    field: "value",
    after: "sk-•••••••f4z1",
    outcome: "applied",
    traceId: "trace-sec-002",
  },
  {
    id: "audit-3",
    timestamp: "2026-05-05T11:55:00.000Z",
    actor: "DesignAgent",
    action: "persona.publish",
    area: "persona",
    target: "persona/agent.md",
    field: "version",
    before: "v13",
    after: "v17",
    outcome: "applied",
    traceId: "trace-per-003",
  },
  {
    id: "audit-4",
    timestamp: "2026-05-05T09:12:18.000Z",
    actor: "system",
    action: "daemon.restart",
    area: "system",
    target: "system/simpleclaw",
    after: "uptime reset",
    outcome: "applied",
    traceId: "trace-sys-004",
  },
  {
    id: "audit-5",
    timestamp: "2026-05-04T22:14:09.000Z",
    actor: "ingki3",
    action: "channel.update",
    area: "channels",
    target: "channels.telegram/allowlist",
    field: "allowlist",
    before: "[•••••42]",
    after: "[•••••42, •••••87]",
    outcome: "applied",
  },
  {
    id: "audit-6",
    timestamp: "2026-05-04T15:30:42.000Z",
    actor: "ingki3",
    action: "cron.toggle",
    area: "cron",
    target: "cron/dreaming.cycle",
    field: "enabled",
    before: "true",
    after: "false",
    outcome: "rolled-back",
    traceId: "trace-cron-006",
  },
  {
    id: "audit-7",
    timestamp: "2026-05-04T11:02:11.000Z",
    actor: "ingki3",
    action: "skill.install",
    area: "skills-recipes",
    target: "skills/gmail-skill",
    after: "installed (v0.4.2)",
    outcome: "applied",
  },
  {
    id: "audit-8",
    timestamp: "2026-05-03T18:20:55.000Z",
    actor: "ingki3",
    action: "memory.delete",
    area: "memory",
    target: "memory/cluster/4",
    before: "32 entries",
    outcome: "applied",
  },
  {
    id: "audit-9",
    timestamp: "2026-05-03T08:45:00.000Z",
    actor: "DesignAgent",
    action: "persona.publish",
    area: "persona",
    target: "persona/style.md",
    field: "version",
    before: "v6",
    after: "v7",
    outcome: "rolled-back",
  },
  {
    id: "audit-10",
    timestamp: "2026-05-02T20:10:33.000Z",
    actor: "ingki3",
    action: "config.update",
    area: "llm-router",
    target: "llm.routing.rules/0",
    field: "weight",
    before: "0.3",
    after: "0.7",
    outcome: "failed",
    traceId: "trace-cfg-010",
  },
  {
    id: "audit-11",
    timestamp: "2026-05-02T09:34:21.000Z",
    actor: "ingki3",
    action: "secret.rotate",
    area: "secrets",
    target: "secrets/anthropic_key",
    field: "value",
    after: "sk-ant-•••••2pX",
    outcome: "failed",
  },
  {
    id: "audit-12",
    timestamp: "2026-05-01T17:48:02.000Z",
    actor: "ingki3",
    action: "skill.uninstall",
    area: "skills-recipes",
    target: "skills/legacy-fetch",
    before: "installed (v0.1.0)",
    outcome: "applied",
  },
  {
    id: "audit-13",
    timestamp: "2026-04-30T07:15:50.000Z",
    actor: "ingki3",
    action: "config.update",
    area: "channels",
    target: "channels.webhook.policy/rate_limit_per_sec",
    field: "rate_limit_per_sec",
    before: "30",
    after: "60",
    outcome: "applied",
  },
  {
    id: "audit-14",
    timestamp: "2026-04-29T12:00:00.000Z",
    actor: "ingki3",
    action: "secret.rotate",
    area: "secrets",
    target: "secrets/google_oauth_token",
    field: "value",
    after: "rotation queued",
    outcome: "pending",
  },
];
