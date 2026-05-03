"use client";

/**
 * Dashboard — admin.pen Screen 01 / DESIGN.md §3 §4 기준의 대시보드.
 *
 * 구성(12-col grid 기반, 페이지 padding 32 / 카드 gap 24 — DESIGN.md §2.5 / §3.3):
 *   1. 상태 카드 4종(`/admin/v1/health`, 5초 폴링) — 데몬, 활성 채널, 24h 메시지, 활성 크론
 *   2. 최근 감사 로그 5건(`/admin/v1/audit?limit=5`)
 *   3. 최근 에러/경고 로그 5건(`/admin/v1/logs?level=error&limit=5`) — error/warn 모두 포함
 *   4. 빠른 진입 카드 4종(LLM / Persona / Skills / Cron)
 *
 * 모든 데이터 카드는 (a) 첫 로드 로딩, (b) 백엔드 에러, (c) 빈 상태를 분리해 표시한다.
 * 헤더는 페이지 h1 + 카드 h2의 2단계로만 구성해 키보드 사용 시 헤딩 점프가 단순하게 유지된다.
 */

import Link from "next/link";
import { useMemo } from "react";
import { useAdminResource } from "@/lib/api/use-admin-resource";
import { StatusCard } from "@/components/dashboard/StatusCard";
import { QuickLinkCard } from "@/components/dashboard/QuickLinkCard";
import { ListPanel, PanelMessage } from "@/components/dashboard/ListPanel";
import { AuditRow, type AuditEntry } from "@/components/domain/AuditRow";
import type { StatusTone } from "@/components/atoms/StatusPill";
import { Badge } from "@/components/atoms/Badge";

// ──────────────────────────────────────────────────────────────────
// 백엔드 응답 타입 — `_handle_health` / `_handle_search_audit` /
// `_handle_search_logs` (src/simpleclaw/channels/admin_api.py) 참조.
// 헬스 추가 필드(`channels_active` 등)는 daemon.health_provider가 합쳐주는
// 선택적 메트릭이므로 모두 optional로 둔다 — 미주입 시 화면은 "—"로 표시.
// ──────────────────────────────────────────────────────────────────

interface HealthResponse {
  status?: string;
  uptime_seconds?: number;
  pending_changes?: boolean;
  daemon?: { state?: "running" | "idle" | "stopped"; uptime_seconds?: number };
  channels?: { active?: number; total?: number };
  messages?: { last_24h?: number };
  cron?: { active?: number; total?: number };
}

interface AuditApiEntry {
  id: string;
  ts: string;
  actor_id: string;
  trace_id: string;
  action: string;
  area: string;
  target: string;
  before?: unknown;
  after?: unknown;
  outcome: string;
  requires_restart: boolean;
  affected_modules: string[];
  undoable: boolean;
  reason: string;
}
interface AuditListResponse {
  entries: AuditApiEntry[];
}

interface LogApiEntry {
  ts?: number | string;
  level?: string;
  message?: string;
  event?: string;
  action_type?: string;
  trace_id?: string;
  [k: string]: unknown;
}
interface LogListResponse {
  entries: LogApiEntry[];
}

// ──────────────────────────────────────────────────────────────────
// 표현 헬퍼
// ──────────────────────────────────────────────────────────────────

const NUMBER_FMT = new Intl.NumberFormat("ko-KR");

function formatNumber(value: number | null | undefined): string | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  return NUMBER_FMT.format(value);
}

function formatRelativeTs(ts: string | number | undefined): string {
  if (ts === undefined || ts === null) return "방금";
  const ms = typeof ts === "number" ? ts * (ts < 1e12 ? 1000 : 1) : Date.parse(ts);
  if (!Number.isFinite(ms)) return String(ts);
  const diff = Date.now() - ms;
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}시간 전`;
  return new Date(ms).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const OUTCOME_TONE: Record<string, StatusTone> = {
  applied: "success",
  pending: "info",
  failed: "error",
  rejected: "warning",
  rolled_back: "neutral",
};

function toAuditEntry(api: AuditApiEntry): AuditEntry {
  // before/after는 마스킹된 string 또는 dict일 수 있어, 그대로 stringify(JSON)해 보여준다.
  // 시크릿은 백엔드가 이미 ref/마스킹 형태로 반환하므로 본 화면이 추가 마스킹할 필요는 없다.
  const stringify = (v: unknown): string | undefined => {
    if (v === undefined || v === null) return undefined;
    if (typeof v === "string") return v;
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  };
  return {
    id: api.id,
    action: api.action,
    target: api.target,
    before: stringify(api.before),
    after: stringify(api.after),
    actor: api.actor_id || "system",
    at: formatRelativeTs(api.ts),
    traceId: api.trace_id || undefined,
    outcome: {
      tone: OUTCOME_TONE[api.outcome] ?? "neutral",
      label: api.outcome,
    },
    undoable: api.undoable,
  };
}

// ──────────────────────────────────────────────────────────────────
// 컴포넌트
// ──────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const health = useAdminResource<HealthResponse>("/admin/v1/health", {
    intervalMs: 5_000,
  });
  const audit = useAdminResource<AuditListResponse>("/admin/v1/audit?limit=5");
  // 백엔드의 `_handle_search_logs`는 단일 level 필터만 슬라이싱하므로 error/warn을
  // 합쳐 보려면 두 번 호출하거나 클라이언트 측에서 합친다. 중요도 우선순위가 error라
  // 우선 error를 부르고, 없으면 warning을 추가로 가져와 합친다.
  const logsError = useAdminResource<LogListResponse>(
    "/admin/v1/logs?level=error&limit=5",
  );
  const logsWarn = useAdminResource<LogListResponse>(
    "/admin/v1/logs?level=warning&limit=5",
  );

  const recentLogs = useMemo<LogApiEntry[]>(() => {
    const merged = [
      ...(logsError.data?.entries ?? []),
      ...(logsWarn.data?.entries ?? []),
    ];
    // ts 내림차순 정렬 후 5건. ts 부재면 입력 순서를 보존.
    return merged
      .map((e, i) => ({ e, i }))
      .sort((a, b) => {
        const ta = toMs(a.e.ts) ?? -a.i;
        const tb = toMs(b.e.ts) ?? -b.i;
        return tb - ta;
      })
      .slice(0, 5)
      .map(({ e }) => e);
  }, [logsError.data, logsWarn.data]);

  // 데몬 상태 카드 — `daemon.state`가 명시되지 않으면 `status === "ok"`로 폴백.
  const daemonState = health.data?.daemon?.state ?? (health.data?.status === "ok" ? "running" : undefined);
  const daemonTone: StatusTone =
    daemonState === "running"
      ? "success"
      : daemonState === "idle"
        ? "info"
        : daemonState === "stopped"
          ? "error"
          : "neutral";
  const daemonLabel =
    daemonState === "running"
      ? "running"
      : daemonState === "idle"
        ? "idle"
        : daemonState === "stopped"
          ? "stopped"
          : "unknown";

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-3xl font-semibold leading-tight text-[--foreground-strong]">
          대시보드
        </h1>
        <p className="text-sm text-[--muted-foreground]">
          데몬 상태와 최근 변경·에러를 한눈에 본다.
        </p>
      </header>

      {/* 1) 상태 카드 4종 — 12-col grid: 모바일 1열 / 태블릿 2열 / 데스크톱 4열. gap 24. */}
      <div
        className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-4"
        aria-label="현재 상태"
      >
        <StatusCard
          title="데몬"
          value={daemonState ? daemonLabel : null}
          status={
            daemonState
              ? { tone: daemonTone, label: daemonLabel }
              : undefined
          }
          hint={
            health.data?.uptime_seconds !== undefined
              ? `가동 ${formatUptime(health.data.uptime_seconds)}`
              : "헬스 응답 대기"
          }
          isLoading={health.isLoading}
          error={health.error ? "헬스 조회 실패" : undefined}
        />
        <StatusCard
          title="활성 채널"
          value={formatNumber(health.data?.channels?.active)}
          hint={
            health.data?.channels?.total !== undefined
              ? `전체 ${formatNumber(health.data.channels.total)}개`
              : "채널 메트릭 미주입"
          }
          isLoading={health.isLoading}
          error={health.error ? "헬스 조회 실패" : undefined}
        />
        <StatusCard
          title="24h 메시지"
          value={formatNumber(health.data?.messages?.last_24h)}
          hint={
            health.data?.messages?.last_24h !== undefined
              ? "지난 24시간 누적"
              : "메시지 메트릭 미주입"
          }
          isLoading={health.isLoading}
          error={health.error ? "헬스 조회 실패" : undefined}
        />
        <StatusCard
          title="활성 크론"
          value={formatNumber(health.data?.cron?.active)}
          hint={
            health.data?.cron?.total !== undefined
              ? `전체 ${formatNumber(health.data.cron.total)}개`
              : "크론 메트릭 미주입"
          }
          isLoading={health.isLoading}
          error={health.error ? "헬스 조회 실패" : undefined}
        />
      </div>

      {/* 2) + 3) 좌우 2열 — 데스크톱 12col에서 6/6, 태블릿 이하 1열. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ListPanel
          title="최근 변경 이력"
          headerRight={
            <Link
              href="/audit"
              className="text-xs text-[--primary] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[--ring] rounded-[--radius-sm]"
            >
              전체 보기
            </Link>
          }
        >
          {audit.isLoading && !audit.data ? (
            <PanelMessage>이력을 불러오는 중…</PanelMessage>
          ) : audit.error ? (
            <PanelMessage tone="error">감사 로그를 불러오지 못했습니다.</PanelMessage>
          ) : !audit.data?.entries.length ? (
            <PanelMessage>최근 변경이 없습니다.</PanelMessage>
          ) : (
            <ul className="flex flex-col">
              {audit.data.entries.slice(0, 5).map((e) => (
                <AuditRow
                  key={e.id}
                  entry={toAuditEntry(e)}
                  // 대시보드 카드에서는 undo/trace 진입을 audit 페이지로 위임.
                  // 클릭 시 audit 라우트로 이동시키는 콜백은 BIZ-43에서 useUndo가 들어오면 합친다.
                  onUndo={() => {
                    window.location.href = "/audit";
                  }}
                  onViewTrace={(traceId) => {
                    window.location.href = `/logs?trace_id=${encodeURIComponent(traceId)}`;
                  }}
                />
              ))}
            </ul>
          )}
        </ListPanel>

        <ListPanel
          title="최근 에러·경고"
          headerRight={
            <Link
              href="/logs?level=error"
              className="text-xs text-[--primary] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[--ring] rounded-[--radius-sm]"
            >
              전체 보기
            </Link>
          }
        >
          {(logsError.isLoading || logsWarn.isLoading) && recentLogs.length === 0 ? (
            <PanelMessage>로그를 불러오는 중…</PanelMessage>
          ) : logsError.error && logsWarn.error ? (
            <PanelMessage tone="error">로그를 불러오지 못했습니다.</PanelMessage>
          ) : recentLogs.length === 0 ? (
            <PanelMessage>최근 24시간 안의 에러·경고가 없습니다.</PanelMessage>
          ) : (
            <ul className="flex flex-col">
              {recentLogs.map((entry, idx) => (
                <li
                  key={`${entry.trace_id ?? "no-trace"}-${idx}`}
                  className="flex flex-col gap-1 border-b border-[--border-divider] px-4 py-3 last:border-b-0"
                >
                  <div className="flex items-center gap-2">
                    <Badge tone={entry.level === "error" ? "danger" : "warning"}>
                      {entry.level ?? "log"}
                    </Badge>
                    <span className="truncate text-sm text-[--foreground-strong]">
                      {entry.message ?? entry.event ?? entry.action_type ?? "(no message)"}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-[--muted-foreground]">
                    <span>{formatRelativeTs(entry.ts)}</span>
                    {entry.trace_id ? (
                      <>
                        <span aria-hidden>·</span>
                        <code className="font-mono">
                          trace {String(entry.trace_id).slice(0, 8)}…
                        </code>
                      </>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </ListPanel>
      </div>

      {/* 4) 빠른 진입 — 4개의 동일 너비 카드. */}
      <section className="flex flex-col gap-3" aria-labelledby="dashboard-quicklinks">
        <h2
          id="dashboard-quicklinks"
          className="text-sm font-semibold text-[--foreground-strong]"
        >
          빠른 진입
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <QuickLinkCard
            href="/llm"
            icon="Brain"
            title="LLM"
            description="프로바이더 라우팅·폴백 체인"
          />
          <QuickLinkCard
            href="/persona"
            icon="BookText"
            title="페르소나"
            description="AGENT.md / USER.md / MEMORY.md"
          />
          <QuickLinkCard
            href="/skills"
            icon="Wrench"
            title="스킬"
            description="스킬·MCP 디스커버리·재시도"
          />
          <QuickLinkCard
            href="/cron"
            icon="Clock"
            title="Cron"
            description="스케줄러·하트비트·실행 이력"
          />
        </div>
      </section>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// 소소한 유틸 — 컴포넌트 본체와 분리.
// ──────────────────────────────────────────────────────────────────

function toMs(ts: unknown): number | null {
  if (ts === undefined || ts === null) return null;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  if (typeof ts === "string") {
    const n = Date.parse(ts);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}
