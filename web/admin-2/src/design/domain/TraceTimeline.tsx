/**
 * TraceTimeline — Domain. trace_id 기반 span lane 차트 (DESIGN.md §3.4).
 *
 * 각 span 은 한 lane 에 그려지고, 시작/종료 시각은 `trace.start` 기준 % 로 환산.
 * S1 박제 단계에서는 인라인 SVG/div 만으로 표현 — chart 라이브러리 도입 없음.
 */

import { cn } from "@/lib/cn";

export interface TraceSpan {
  id: string;
  name: string;
  /** 시작 (ms, trace.start 기준 상대값). */
  startMs: number;
  /** 종료 (ms, trace.start 기준 상대값). */
  endMs: number;
  /** 의미 색상 — semantic tone. */
  tone?: "primary" | "success" | "warning" | "error" | "muted";
}

export interface TraceTimelineProps {
  spans: TraceSpan[];
  /** 전체 trace 의 길이 (ms). 미지정 시 마지막 span 종료시각. */
  totalMs?: number;
  className?: string;
}

const TONE_BG: Record<NonNullable<TraceSpan["tone"]>, string> = {
  primary: "bg-(--primary)",
  success: "bg-(--color-success)",
  warning: "bg-(--color-warning)",
  error: "bg-(--color-error)",
  muted: "bg-(--muted-foreground)",
};

export function TraceTimeline({
  spans,
  totalMs,
  className,
}: TraceTimelineProps) {
  const total =
    totalMs ??
    spans.reduce((acc, s) => Math.max(acc, s.endMs), 0);
  const safeTotal = total > 0 ? total : 1;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-4",
        className,
      )}
    >
      <header className="flex items-center justify-between text-xs text-(--muted-foreground)">
        <span>Trace timeline</span>
        <span className="tabular-nums">{total} ms</span>
      </header>
      <ul className="flex flex-col gap-1.5">
        {spans.map((span) => {
          const left = (span.startMs / safeTotal) * 100;
          const width = ((span.endMs - span.startMs) / safeTotal) * 100;
          return (
            <li
              key={span.id}
              className="grid grid-cols-[140px_1fr] items-center gap-2 text-xs"
            >
              <span className="truncate text-(--foreground)" title={span.name}>
                {span.name}
              </span>
              <span className="relative h-3 rounded-(--radius-pill) bg-(--surface)">
                <span
                  className={cn(
                    "absolute top-0 h-3 rounded-(--radius-pill)",
                    TONE_BG[span.tone ?? "primary"],
                  )}
                  style={{
                    left: `${left}%`,
                    width: `${Math.max(width, 0.5)}%`,
                  }}
                  aria-label={`${span.name} ${span.startMs}-${span.endMs}ms`}
                />
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
