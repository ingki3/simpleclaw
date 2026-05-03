"use client";

/**
 * DreamingObservabilityPanel — Memory 화면(BIZ-81) 의 KPI / 진단 카드.
 *
 * 책임:
 *  - ``/memory/dreaming/status`` 한 번 호출로 last_run / next_run / 7일 KPI / 거절률 노출.
 *  - "최근 회차" 목록을 ``/memory/dreaming/runs`` 에서 받아 상위 N건 (기본 5) 표시.
 *  - 진단 메시지(``trigger_message`` + blockers) 를 명시적으로 노출 — 운영자가
 *    "왜 5-03 에 dreaming 이 갱신되지 않았나" 를 즉시 진단할 수 있게 한다 (BIZ-66 §3-K).
 *  - ``metrics_enabled=false`` 면 패널 자체를 "메트릭 비활성" 안내로 폴백.
 *
 * 비책임:
 *  - 자체 polling 없음. 외부에서 ``refreshKey`` prop 으로 강제 갱신을 트리거할 수 있고,
 *    카드 자체에 새로고침 버튼도 둔다 — 드리밍 트리거 후 상태 polling 이 끝나면
 *    상위 페이지가 ``refreshKey`` 를 +1 해 자동 동기화시킨다.
 *  - 액션(트리거/취소) 없음. 트리거는 DreamingProgressCard 가 담당.
 *
 * 디자인 결정:
 *  - 한 카드 안에 두 시각 단위(7일 KPI vs. 최근 회차 목록) 가 공존하므로 시각 위계는
 *    `최근 결과 -> KPI -> 진단 -> 사이클 표` 순. 진단(blockers) 은 회차 표보다 위에
 *    두어 "왜 안 돌았나" 라는 1순위 질문에 즉시 답한다.
 *  - 색은 StatusPill 의 success/warning/error 톤만 사용 — DESIGN.md §5 (색만으로
 *    상태 표현 금지) 준수해 dot+라벨 조합으로 표시.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CircleAlert,
  CircleCheck,
  Clock,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import {
  type DreamingRun,
  type DreamingRunStatus,
  type DreamingStatusResponse,
  getDreamingStatusV2,
  listDreamingRuns,
} from "@/lib/api/dreaming-runs";
import { cn } from "@/lib/cn";

export interface DreamingObservabilityPanelProps {
  /** 외부 변화에 맞춰 패널을 강제 갱신할 때 +1. 미지정이면 마운트 시 1회만 로드. */
  refreshKey?: number;
  /** 초기 로드 + 새로고침 시 함께 가져올 최근 회차 행 수. 기본 5. */
  recentLimit?: number;
}

interface PanelState {
  status: DreamingStatusResponse | null;
  runs: DreamingRun[];
  loading: boolean;
  error: string | null;
}

/** skip_reason 식별자 → 사람용 한국어. 백엔드가 새 reason 을 추가해도 화면을 깨지 않게 폴백. */
const SKIP_REASON_LABELS: Record<string, string> = {
  no_messages: "메시지 없음",
  preflight_failed: "보호 섹션 검증 실패",
  midwrite_aborted: "쓰기 도중 중단",
  empty_results: "결과 비어 있음",
};

function skipReasonLabel(reason: string | null): string {
  if (!reason) return "—";
  return SKIP_REASON_LABELS[reason] ?? reason;
}

function statusTone(status: DreamingRunStatus): StatusTone {
  switch (status) {
    case "success":
      return "success";
    case "skip":
      return "warning";
    case "error":
      return "error";
    case "running":
      return "info";
    default:
      return "neutral";
  }
}

function statusLabel(status: DreamingRunStatus): string {
  switch (status) {
    case "success":
      return "성공";
    case "skip":
      return "건너뜀";
    case "error":
      return "오류";
    case "running":
      return "진행 중";
    default:
      return status;
  }
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

function formatRate(rate: number | null): string {
  if (rate === null || rate === undefined) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

export function DreamingObservabilityPanel({
  refreshKey,
  recentLimit = 5,
}: DreamingObservabilityPanelProps) {
  const [state, setState] = useState<PanelState>({
    status: null,
    runs: [],
    loading: true,
    error: null,
  });

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      // status 와 runs 를 병렬로 호출 — 어느 한쪽이 503 이어도 나머지는 그릴 수 있도록 분리.
      const [statusRes, runsRes] = await Promise.allSettled([
        getDreamingStatusV2(),
        listDreamingRuns(recentLimit),
      ]);
      const status =
        statusRes.status === "fulfilled" ? statusRes.value : null;
      const runs =
        runsRes.status === "fulfilled" ? runsRes.value.runs : [];
      // 둘 다 실패면 에러 메시지를 노출. status 만 있으면 metrics_enabled=false 분기로 처리.
      let error: string | null = null;
      if (statusRes.status === "rejected" && runsRes.status === "rejected") {
        error =
          statusRes.reason instanceof Error
            ? statusRes.reason.message
            : String(statusRes.reason);
      }
      setState({ status, runs, loading: false, error });
    } catch (e) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      }));
    }
  }, [recentLimit]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  return (
    <section
      aria-labelledby="dreaming-obs-title"
      className="flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5"
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2
            id="dreaming-obs-title"
            className="flex items-center gap-2 text-sm font-semibold text-(--foreground-strong)"
          >
            <Activity size={14} aria-hidden /> 드리밍 관측성
          </h2>
          <p className="mt-1 text-xs text-(--muted-foreground)">
            최근 사이클 결과 · 7일 KPI · 다음 시도 진단을 한곳에서 봅니다.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void refresh()}
          disabled={state.loading}
          leftIcon={
            state.loading ? (
              <Loader2 size={12} aria-hidden className="animate-spin" />
            ) : (
              <RefreshCw size={12} aria-hidden />
            )
          }
        >
          새로고침
        </Button>
      </header>

      {state.error ? (
        <div className="flex items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) px-3 py-2 text-xs text-(--color-error)">
          <AlertTriangle size={14} aria-hidden className="mt-0.5" />
          <div>
            <div className="font-medium">관측 데이터를 불러오지 못했어요</div>
            <div className="text-(--muted-foreground)">{state.error}</div>
          </div>
        </div>
      ) : null}

      {state.loading && !state.status ? (
        <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-4 text-center text-xs text-(--muted-foreground)">
          불러오는 중…
        </div>
      ) : null}

      {state.status ? (
        <PanelBody status={state.status} runs={state.runs} />
      ) : null}
    </section>
  );
}

interface PanelBodyProps {
  status: DreamingStatusResponse;
  runs: DreamingRun[];
}

function PanelBody({ status, runs }: PanelBodyProps) {
  const last = status.last_run;
  const lastSuccess = status.last_successful_run;
  const kpi = status.kpi_7d;

  return (
    <div className="flex flex-col gap-3">
      {/* 최근 결과 — last_run + last_successful_run */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <SummaryTile
          label="마지막 회차"
          value={formatDateTime(last?.started_at ?? null)}
          accent={
            last ? (
              <StatusPill tone={statusTone(last.status)}>
                {statusLabel(last.status)}
              </StatusPill>
            ) : (
              <StatusPill tone="neutral">실행 이력 없음</StatusPill>
            )
          }
          hint={
            last && last.status === "skip"
              ? `사유: ${skipReasonLabel(last.skip_reason)}`
              : last && last.status === "error"
                ? truncate(last.error ?? "", 80)
                : last
                  ? `소요 ${formatDuration(last.duration_seconds)} · 입력 ${last.input_msg_count}건 → 인사이트 ${last.generated_insight_count}건`
                  : null
          }
        />
        <SummaryTile
          label="마지막 성공"
          value={formatDateTime(lastSuccess?.started_at ?? null)}
          accent={
            lastSuccess ? (
              <StatusPill tone="success">
                <CircleCheck size={11} aria-hidden /> success
              </StatusPill>
            ) : (
              <StatusPill tone="warning">최근 7일 내 성공 없음</StatusPill>
            )
          }
          hint={
            lastSuccess
              ? `인사이트 ${lastSuccess.generated_insight_count}건 · 차단 ${lastSuccess.rejected_count}건`
              : null
          }
        />
      </div>

      {/* 다음 시도 + 진단 메시지 */}
      <div className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-xs">
        <div className="flex items-center gap-2 text-(--foreground)">
          <Clock size={12} aria-hidden />
          <span className="font-medium">다음 시도</span>
          <span className="text-(--muted-foreground)">
            {formatDateTime(status.next_run)}
            {status.overnight_hour !== null
              ? ` (야간 ${String(status.overnight_hour).padStart(2, "0")}:00`
              : ""}
            {status.idle_threshold_seconds !== null
              ? ` · idle ${status.idle_threshold_seconds}s)`
              : status.overnight_hour !== null
                ? ")"
                : ""}
          </span>
        </div>
        {status.trigger_message ? (
          <div className="mt-1 text-(--muted-foreground)">
            {status.trigger_message}
          </div>
        ) : null}
        {status.trigger_blockers.length > 0 ? (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-(--muted-foreground)">
            {status.trigger_blockers.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {/* 7일 KPI */}
      {kpi ? (
        <KpiGrid kpi={kpi} rejection={status.rejection} />
      ) : (
        <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-2 text-xs text-(--muted-foreground)">
          KPI 메트릭 비활성 — ``runs_file`` 이 데몬 부팅 시 주입되지 않았어요.
        </div>
      )}

      {/* 최근 회차 표 */}
      {runs.length > 0 ? (
        <RecentRunsTable runs={runs} />
      ) : status.metrics_enabled ? (
        <div className="rounded-(--radius-m) border border-dashed border-(--border) bg-(--surface) px-3 py-2 text-xs text-(--muted-foreground)">
          아직 기록된 사이클이 없어요 — 첫 드리밍이 돌면 여기에 누적됩니다.
        </div>
      ) : null}
    </div>
  );
}

interface SummaryTileProps {
  label: string;
  value: string;
  accent: React.ReactNode;
  hint?: string | null;
}

function SummaryTile({ label, value, accent, hint }: SummaryTileProps) {
  return (
    <div className="flex flex-col gap-1 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2">
      <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide text-(--muted-foreground)">
        <span>{label}</span>
        {accent}
      </div>
      <div className="text-xs font-medium text-(--foreground-strong)">
        {value}
      </div>
      {hint ? (
        <div className="text-[11px] text-(--muted-foreground)">{hint}</div>
      ) : null}
    </div>
  );
}

interface KpiGridProps {
  kpi: NonNullable<DreamingStatusResponse["kpi_7d"]>;
  rejection: DreamingStatusResponse["rejection"];
}

function KpiGrid({ kpi, rejection }: KpiGridProps) {
  // 7일 윈도우 결과 처리율 — total_runs 가 0 이면 "—".
  const totalForRate = kpi.success + kpi.skip + kpi.error;
  const successRate =
    totalForRate > 0 ? `${((kpi.success / totalForRate) * 100).toFixed(0)}%` : "—";
  return (
    <div className="rounded-(--radius-m) border border-(--border) bg-(--surface) p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-(--foreground-strong)">
          최근 {kpi.window_days}일 KPI
        </h3>
        <span className="text-[10px] text-(--muted-foreground)">
          총 {kpi.total_runs}회 시도
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <KpiCell label="성공" value={String(kpi.success)} suffix={successRate} tone="success" />
        <KpiCell label="건너뜀" value={String(kpi.skip)} tone="warning" />
        <KpiCell label="오류" value={String(kpi.error)} tone={kpi.error > 0 ? "error" : "neutral"} />
        <KpiCell label="입력 메시지" value={kpi.input_msg_total.toLocaleString()} />
        <KpiCell
          label="생성 인사이트"
          value={kpi.insight_total.toLocaleString()}
        />
        <KpiCell
          label="차단 인사이트"
          value={kpi.rejected_total.toLocaleString()}
          tone={kpi.rejected_total > 0 ? "warning" : "neutral"}
        />
      </div>

      {/* skip 분해 — empty 면 표시하지 않는다(공간 절약). */}
      {Object.keys(kpi.skip_breakdown).length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
          <span className="text-(--muted-foreground)">건너뜀 사유:</span>
          {Object.entries(kpi.skip_breakdown).map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1 rounded-(--radius-pill) border border-(--border) bg-(--surface) px-2 py-0.5 text-(--foreground)"
            >
              {skipReasonLabel(k)} <strong className="font-mono">{v}</strong>
            </span>
          ))}
        </div>
      ) : null}

      {/* 운영자 리뷰 거절률 — 별도 신호임을 라벨로 명시. */}
      <div className="mt-2 flex items-center justify-between gap-2 border-t border-(--border) pt-2 text-[11px] text-(--muted-foreground)">
        <span>운영자 리뷰 거절률</span>
        <span>
          {rejection.reviewed > 0
            ? `${rejection.rejected}/${rejection.reviewed} (${formatRate(rejection.rate)})`
            : "리뷰 이력 없음"}
        </span>
      </div>
    </div>
  );
}

interface KpiCellProps {
  label: string;
  value: string;
  suffix?: string;
  tone?: StatusTone;
}

function KpiCell({ label, value, suffix, tone = "neutral" }: KpiCellProps) {
  // tone 은 숫자 자체가 아니라 라벨 옆 dot 으로만 표현 — 색만으로 의미 전달 금지.
  return (
    <div className="flex flex-col gap-0.5 rounded-(--radius-sm) bg-(--card) px-2 py-1.5">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-(--muted-foreground)">
        <span
          aria-hidden
          className={cn(
            "inline-block h-1.5 w-1.5 rounded-(--radius-pill)",
            tone === "success" && "bg-(--color-success)",
            tone === "warning" && "bg-(--color-warning)",
            tone === "error" && "bg-(--color-error)",
            tone === "info" && "bg-(--color-info)",
            tone === "neutral" && "bg-(--muted-foreground)",
          )}
        />
        <span>{label}</span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-base font-semibold text-(--foreground-strong)">
          {value}
        </span>
        {suffix ? (
          <span className="text-[10px] text-(--muted-foreground)">{suffix}</span>
        ) : null}
      </div>
    </div>
  );
}

function RecentRunsTable({ runs }: { runs: DreamingRun[] }) {
  return (
    <div className="overflow-hidden rounded-(--radius-m) border border-(--border) bg-(--surface)">
      <table className="w-full text-[11px]">
        <thead className="bg-(--card) text-left text-(--muted-foreground)">
          <tr>
            <th className="px-2 py-1.5 font-medium">시각</th>
            <th className="px-2 py-1.5 font-medium">상태</th>
            <th className="px-2 py-1.5 font-medium text-right">입력</th>
            <th className="px-2 py-1.5 font-medium text-right">인사이트</th>
            <th className="px-2 py-1.5 font-medium text-right">차단</th>
            <th className="px-2 py-1.5 font-medium">소요</th>
            <th className="px-2 py-1.5 font-medium">사유</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.id}
              className="border-t border-(--border) align-top text-(--foreground)"
            >
              <td className="px-2 py-1.5 font-mono">
                {formatDateTime(r.started_at)}
              </td>
              <td className="px-2 py-1.5">
                <StatusPill tone={statusTone(r.status)}>
                  {statusLabel(r.status)}
                </StatusPill>
              </td>
              <td className="px-2 py-1.5 text-right font-mono">
                {r.input_msg_count}
              </td>
              <td className="px-2 py-1.5 text-right font-mono">
                {r.generated_insight_count}
              </td>
              <td className="px-2 py-1.5 text-right font-mono">
                {r.rejected_count}
              </td>
              <td className="px-2 py-1.5 font-mono">
                {formatDuration(r.duration_seconds)}
              </td>
              <td className="px-2 py-1.5 text-(--muted-foreground)">
                {r.status === "error" ? (
                  <span className="inline-flex items-center gap-1 text-(--color-error)">
                    <CircleAlert size={11} aria-hidden />
                    {truncate(r.error ?? "", 50)}
                  </span>
                ) : r.status === "skip" ? (
                  skipReasonLabel(r.skip_reason)
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function truncate(s: string, n: number): string {
  if (!s) return "—";
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
