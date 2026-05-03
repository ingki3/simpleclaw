"use client";

/**
 * CronJobRow — DESIGN.md §3.4 + §4.7 Compact Table.
 *
 * 컬럼: 이름 / 스케줄 / 다음 실행 / 상태 / circuit / 액션.
 * 행 높이 44, 셀 padding [8,12], radius-none — 테이블 컨텍스트의 dense 행으로 의도.
 */

import { Pause, Play, Trash2 } from "lucide-react";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";

export interface CronJob {
  id: string;
  name: string;
  /** 휴먼 가능한 스케줄 — 예: '매일 09:00' 또는 '0 9 * * *'. */
  schedule: string;
  /** ISO 또는 휴먼 가능한 "다음 실행" 문자열. */
  nextRun: string;
  status: { tone: StatusTone; label: string };
  /** circuit breaker 상태 — closed/half-open/open. */
  circuit: "closed" | "half-open" | "open";
  paused: boolean;
}

export interface CronJobRowProps {
  job: CronJob;
  onTogglePause: (id: string) => void;
  onDelete: (id: string) => void;
}

const CIRCUIT_TONE: Record<CronJob["circuit"], "success" | "warning" | "danger"> = {
  closed: "success",
  "half-open": "warning",
  open: "danger",
};

export function CronJobRow({ job, onTogglePause, onDelete }: CronJobRowProps) {
  return (
    <tr className="h-11 border-b border-(--border) text-sm last:border-b-0">
      <td className="px-3 font-medium text-(--foreground-strong)">{job.name}</td>
      <td className="px-3 font-mono text-xs text-(--muted-foreground)">
        {job.schedule}
      </td>
      <td className="px-3 text-(--muted-foreground)">{job.nextRun}</td>
      <td className="px-3">
        <StatusPill tone={job.status.tone}>{job.status.label}</StatusPill>
      </td>
      <td className="px-3">
        <Badge tone={CIRCUIT_TONE[job.circuit]}>{job.circuit}</Badge>
      </td>
      <td className="px-3">
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            aria-label={job.paused ? "재개" : "일시정지"}
            onClick={() => onTogglePause(job.id)}
          >
            {job.paused ? (
              <Play size={14} aria-hidden />
            ) : (
              <Pause size={14} aria-hidden />
            )}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label="삭제"
            onClick={() => onDelete(job.id)}
          >
            <Trash2 size={14} aria-hidden />
          </Button>
        </div>
      </td>
    </tr>
  );
}
