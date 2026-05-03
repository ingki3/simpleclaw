"use client";

/**
 * LogRow — 가상 스크롤 리스트의 한 행. 고정 높이 44px(DESIGN.md §4.7 compact 행).
 *
 * 컬럼 구성: [시각] [레벨 배지] [모듈/액션] [요약 메시지] [trace 칩]
 * 행 자체가 버튼 역할을 해 클릭 시 trace 상세 Drawer를 연다.
 *
 * 새 행 진입 애니메이션:
 *  ``isFresh``가 true면 ``data-fresh="true"`` 속성을 달아 globals.css의 keyframe이
 *  배경 페이드를 0.6s 동안 적용하게 한다(자동 새로고침 ON일 때 새 항목 강조).
 */

import { Badge, type BadgeTone } from "@/components/atoms/Badge";
import { cn } from "@/lib/cn";
import type { LogApiEntry, LogLevel } from "@/lib/api/logs";
import { normalizeLevel } from "@/lib/api/logs";

const LEVEL_TONE: Record<LogLevel, BadgeTone> = {
  DEBUG: "neutral",
  INFO: "info",
  WARNING: "warning",
  ERROR: "danger",
};

const LEVEL_LABEL: Record<LogLevel, string> = {
  DEBUG: "debug",
  INFO: "info",
  WARNING: "warn",
  ERROR: "error",
};

function summarize(entry: LogApiEntry): string {
  // 입력 요약을 최우선으로 — 사람이 읽기 좋다.
  if (entry.input_summary) return entry.input_summary;
  if (entry.output_summary) return entry.output_summary;
  if (entry.status && entry.status !== "success") return `status: ${entry.status}`;
  if (entry.action_type) return entry.action_type;
  return "(no summary)";
}

function formatTime(ts: string | undefined): string {
  if (!ts) return "—";
  // ISO 형식 가정. 시간만 잘라서 보여주고 hover 시 풀 timestamp(title).
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export interface LogRowProps {
  entry: LogApiEntry;
  onSelect: () => void;
  isFresh?: boolean;
}

export function LogRow({ entry, onSelect, isFresh }: LogRowProps) {
  const level = normalizeLevel(entry.level) ?? "INFO";
  const tone = LEVEL_TONE[level];
  const label = LEVEL_LABEL[level];
  const message = summarize(entry);
  const trace = entry.trace_id ?? "";

  return (
    <button
      type="button"
      data-fresh={isFresh ? "true" : undefined}
      onClick={onSelect}
      title={entry.timestamp ?? undefined}
      className={cn(
        "log-row flex h-[44px] w-full items-center gap-3 border-b border-[--border] px-4 text-left text-sm",
        "hover:bg-[--surface] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[--ring]",
      )}
    >
      <span className="w-[80px] shrink-0 font-mono text-xs text-[--muted-foreground]">
        {formatTime(entry.timestamp)}
      </span>
      <span className="w-[64px] shrink-0">
        <Badge tone={tone}>{label}</Badge>
      </span>
      <span className="w-[180px] shrink-0 truncate font-mono text-xs text-[--foreground-strong]">
        {entry.action_type || "—"}
      </span>
      <span className="min-w-0 flex-1 truncate text-[--foreground]">{message}</span>
      {trace ? (
        <span className="shrink-0 font-mono text-xs text-[--muted-foreground]">
          trace {trace.slice(0, 8)}
        </span>
      ) : null}
    </button>
  );
}
