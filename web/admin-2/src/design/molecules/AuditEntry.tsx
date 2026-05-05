/**
 * AuditEntry — Molecular. actor·action·target·outcome·trace·undo (DESIGN.md §3.2, §4.4).
 *
 * 한 줄에 모든 메타가 들어가도록 압축 표시. trace_id 는 복사 가능 토큰.
 * undo 콜백이 주어지면 우측 끝에 액션이 노출된다.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { StatusPill, type StatusTone } from "../atoms/StatusPill";
import { Code } from "../atoms/Code";

export interface AuditEntryProps {
  actor: string;
  action: string;
  target: ReactNode;
  /** "applied" | "rolled-back" 등 — semantic tone 으로 자동 매핑. */
  outcome: "applied" | "rolled-back" | "failed" | "pending";
  traceId?: string;
  /** ISO 또는 사람 친화 시간 문자열 — 형식은 부모가 결정. */
  timestamp?: string;
  /** 우측 끝 액션 (예: undo 버튼). */
  action_slot?: ReactNode;
  className?: string;
}

const OUTCOME_TONE: Record<AuditEntryProps["outcome"], StatusTone> = {
  applied: "success",
  "rolled-back": "warning",
  failed: "error",
  pending: "info",
};

const OUTCOME_LABEL: Record<AuditEntryProps["outcome"], string> = {
  applied: "적용",
  "rolled-back": "되돌림",
  failed: "실패",
  pending: "대기",
};

export function AuditEntry({
  actor,
  action,
  target,
  outcome,
  traceId,
  timestamp,
  action_slot,
  className,
}: AuditEntryProps) {
  return (
    <article
      className={cn(
        "flex items-center gap-3 border-b border-(--border) py-2 text-sm",
        className,
      )}
    >
      <StatusPill tone={OUTCOME_TONE[outcome]}>
        {OUTCOME_LABEL[outcome]}
      </StatusPill>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
        <span className="font-medium text-(--foreground)">{actor}</span>
        <span className="text-(--muted-foreground)">·</span>
        <span className="text-(--foreground)">{action}</span>
        <span className="text-(--muted-foreground)">·</span>
        <span className="truncate text-(--foreground)">{target}</span>
      </div>
      {timestamp ? (
        <span className="shrink-0 text-xs text-(--muted-foreground)">
          {timestamp}
        </span>
      ) : null}
      {traceId ? (
        <Code className="shrink-0">{`trace ${traceId.slice(0, 8)}`}</Code>
      ) : null}
      {action_slot ? <div className="shrink-0">{action_slot}</div> : null}
    </article>
  );
}
