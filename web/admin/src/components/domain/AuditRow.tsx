"use client";

/**
 * AuditRow — DESIGN.md §4.4 Audit Trail.
 *
 * 1행 = 1개의 변경 이벤트. actor·action·target·outcome·trace_id·undo가 한 줄에 압축된다.
 * 시크릿 변경의 before/after는 호출부에서 마스킹된 채로 넘겨야 한다 (본 컴포넌트는 표시만 담당).
 */

import { History, ExternalLink } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";

export interface AuditEntry {
  id: string;
  /** "config.update" 등 도메인 액션 식별자. */
  action: string;
  /** "llm.providers.claude.model" 등 점-구분 키. */
  target: string;
  /** before/after는 마스킹/요약된 값을 받는다. */
  before?: string;
  after?: string;
  actor: string;
  /** ISO 타임스탬프 또는 휴먼 텍스트. */
  at: string;
  traceId?: string;
  outcome: { tone: StatusTone; label: string };
  /** undo 가능 여부 — 시크릿 회전·재시작은 false. */
  undoable: boolean;
}

export interface AuditRowProps {
  entry: AuditEntry;
  onUndo: (id: string) => void;
  onViewTrace: (traceId: string) => void;
}

export function AuditRow({ entry, onUndo, onViewTrace }: AuditRowProps) {
  return (
    <li className="flex flex-col gap-1 border-b border-(--border) px-3 py-3 text-sm last:border-b-0">
      <div className="flex items-center gap-2">
        <History
          size={14}
          aria-hidden
          className="text-(--muted-foreground)"
        />
        <span className="font-medium text-(--foreground-strong)">
          {entry.action}
        </span>
        <code className="font-mono text-xs text-(--muted-foreground)">
          {entry.target}
        </code>
        <StatusPill tone={entry.outcome.tone} className="ml-auto">
          {entry.outcome.label}
        </StatusPill>
      </div>
      {entry.before || entry.after ? (
        <div className="flex items-center gap-2 pl-5 font-mono text-xs text-(--muted-foreground)">
          <span>{entry.before ?? "—"}</span>
          <span aria-hidden>→</span>
          <span className="text-(--foreground)">{entry.after ?? "—"}</span>
        </div>
      ) : null}
      <div className="flex items-center gap-3 pl-5 text-xs text-(--muted-foreground)">
        <span>{entry.actor}</span>
        <span aria-hidden>·</span>
        <span>{entry.at}</span>
        {entry.traceId ? (
          <>
            <span aria-hidden>·</span>
            <code className="font-mono">trace {entry.traceId.slice(0, 8)}…</code>
          </>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          {entry.undoable ? (
            <Button variant="ghost" size="sm" onClick={() => onUndo(entry.id)}>
              되돌리기
            </Button>
          ) : null}
          {entry.traceId ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onViewTrace(entry.traceId!)}
              aria-label="트레이스 보기"
            >
              <ExternalLink size={12} aria-hidden />
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}
