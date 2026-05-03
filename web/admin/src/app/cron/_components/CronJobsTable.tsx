"use client";

/**
 * Cron 잡 테이블 — 이름 / 표현식 / 다음 실행 / 마지막 결과 / 활성 토글 / 액션.
 *
 * 행 클릭은 상세 Drawer 토글이며, 토글·Run-now·삭제 버튼은 ``stopPropagation``
 * 로 행 클릭과 분리한다. 비활성 잡은 row 전체에 ``opacity-60``을 걸어 dim 처리
 * 한다(DESIGN.md §4.6 비활성 표면 가이드를 따름).
 */

import { Play, Trash2 } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Switch } from "@/components/atoms/Switch";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Badge } from "@/components/atoms/Badge";
import { cn } from "@/lib/cn";
import type { CronJob } from "@/lib/cron/types";
import { getNextRuns } from "@/lib/cron/expression";

export interface CronJobsTableProps {
  jobs: CronJob[];
  /** 진행 중인 mutation을 받아 버튼을 일시 disable한다. */
  busyJobName: string | null;
  onSelect: (job: CronJob) => void;
  onToggleEnabled: (job: CronJob, next: boolean) => void;
  onRunNow: (job: CronJob) => void;
  onDelete: (job: CronJob) => void;
}

export function CronJobsTable({
  jobs,
  busyJobName,
  onSelect,
  onToggleEnabled,
  onRunNow,
  onDelete,
}: CronJobsTableProps) {
  return (
    <div className="overflow-hidden rounded-[--radius-l] border border-[--border] bg-[--card]">
      <table className="w-full text-sm">
        <thead className="bg-[--surface] text-xs text-[--muted-foreground]">
          <tr className="text-left">
            <th className="px-3 py-2 font-medium">이름</th>
            <th className="px-3 py-2 font-medium">표현식</th>
            <th className="px-3 py-2 font-medium">다음 실행</th>
            <th className="px-3 py-2 font-medium">마지막 결과</th>
            <th className="px-3 py-2 font-medium">활성</th>
            <th className="px-3 py-2 text-right font-medium">액션</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <CronJobsTableRow
              key={job.name}
              job={job}
              busy={busyJobName === job.name}
              onSelect={onSelect}
              onToggleEnabled={onToggleEnabled}
              onRunNow={onRunNow}
              onDelete={onDelete}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface RowProps {
  job: CronJob;
  busy: boolean;
  onSelect: (job: CronJob) => void;
  onToggleEnabled: (job: CronJob, next: boolean) => void;
  onRunNow: (job: CronJob) => void;
  onDelete: (job: CronJob) => void;
}

const LAST_RUN_TONE: Record<string, StatusTone> = {
  success: "success",
  failed: "error",
  running: "info",
  skipped: "neutral",
};

function CronJobsTableRow({
  job,
  busy,
  onSelect,
  onToggleEnabled,
  onRunNow,
  onDelete,
}: RowProps) {
  // 다음 실행 시각 — 비활성이거나 표현식이 깨졌으면 표시하지 않는다.
  let nextRunLabel = "—";
  if (job.enabled) {
    try {
      const next = getNextRuns(job.cronExpression, 1)[0];
      if (next) nextRunLabel = formatRelative(next);
    } catch {
      nextRunLabel = "표현식 오류";
    }
  } else {
    nextRunLabel = "비활성";
  }

  const lastRun = job.lastRun;
  const circuitOpen = job.consecutiveFailures >= job.circuitBreakThreshold && job.circuitBreakThreshold > 0;

  return (
    <tr
      onClick={() => onSelect(job)}
      className={cn(
        "h-12 cursor-pointer border-t border-[--border] transition-colors hover:bg-[--surface]",
        !job.enabled && "opacity-60",
      )}
    >
      <td className="px-3">
        <div className="flex items-center gap-2">
          <span className="font-medium text-[--foreground-strong]">{job.name}</span>
          {circuitOpen ? <Badge tone="danger">circuit-break</Badge> : null}
        </div>
      </td>
      <td className="px-3 font-mono text-xs text-[--muted-foreground]">
        {job.cronExpression}
      </td>
      <td className="px-3 text-[--muted-foreground]">{nextRunLabel}</td>
      <td className="px-3">
        {lastRun ? (
          <div className="flex items-center gap-2">
            <StatusPill tone={LAST_RUN_TONE[lastRun.status] ?? "neutral"}>
              {LAST_RUN_LABEL[lastRun.status] ?? lastRun.status}
            </StatusPill>
            <span className="text-xs text-[--muted-foreground]">
              {formatRelative(new Date(lastRun.startedAt))}
            </span>
          </div>
        ) : (
          <span className="text-xs text-[--muted-foreground]">기록 없음</span>
        )}
      </td>
      <td className="px-3" onClick={stop}>
        <Switch
          checked={job.enabled}
          onCheckedChange={(next) => onToggleEnabled(job, next)}
          label={`${job.name} 활성 토글`}
          disabled={busy}
        />
      </td>
      <td className="px-3 text-right" onClick={stop}>
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Play size={14} aria-hidden />}
            disabled={busy}
            onClick={() => onRunNow(job)}
          >
            Run now
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${job.name} 삭제`}
            disabled={busy}
            onClick={() => onDelete(job)}
          >
            <Trash2 size={14} aria-hidden />
          </Button>
        </div>
      </td>
    </tr>
  );
}

const LAST_RUN_LABEL: Record<string, string> = {
  success: "성공",
  failed: "실패",
  running: "실행 중",
  skipped: "건너뜀",
};

function stop(e: React.MouseEvent) {
  // 토글/액션 셀 안의 클릭이 행 단위 onSelect로 전파되지 않게 한다.
  e.stopPropagation();
}

/** 가까운 시점은 상대 표기, 먼 시점은 절대 표기 — 운영자가 위치를 잡기 쉽게. */
function formatRelative(d: Date): string {
  const now = Date.now();
  const diff = d.getTime() - now;
  const absMin = Math.round(Math.abs(diff) / 60_000);

  if (Math.abs(diff) < 60_000) return diff > 0 ? "잠시 후" : "방금";
  if (absMin < 60) return diff > 0 ? `${absMin}분 후` : `${absMin}분 전`;
  const absHr = Math.round(absMin / 60);
  if (absHr < 24) return diff > 0 ? `${absHr}시간 후` : `${absHr}시간 전`;
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return `${d.getMonth() + 1}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
