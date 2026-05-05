/**
 * MemoryClusterMap — Domain. 클러스터 도넛/리스트 + 상위 키워드 (DESIGN.md §3.4).
 *
 * Memory dreaming 결과의 클러스터 분포를 한 카드에 박제.
 * 좌측: stacked-bar (클러스터별 비율). 우측: top keyword 리스트.
 *
 * S1 박제 단계에서는 인라인 SVG/div 만 — chart 라이브러리 미도입.
 */

import { cn } from "@/lib/cn";

export interface MemoryCluster {
  id: string;
  label: string;
  count: number;
  /** 색상 토큰 — 미지정 시 hash 기반 자동 (현재는 기본 5종 색상 회전). */
  tone?: "primary" | "success" | "warning" | "error" | "info";
  /** 상위 키워드 (최대 3개 권장). */
  keywords?: string[];
}

export interface MemoryClusterMapProps {
  clusters: MemoryCluster[];
  className?: string;
}

const TONE_CYCLE: Array<NonNullable<MemoryCluster["tone"]>> = [
  "primary",
  "success",
  "info",
  "warning",
  "error",
];

const TONE_BG: Record<NonNullable<MemoryCluster["tone"]>, string> = {
  primary: "bg-(--primary)",
  success: "bg-(--color-success)",
  warning: "bg-(--color-warning)",
  error: "bg-(--color-error)",
  info: "bg-(--color-info)",
};

const TONE_DOT: Record<NonNullable<MemoryCluster["tone"]>, string> = TONE_BG;

export function MemoryClusterMap({
  clusters,
  className,
}: MemoryClusterMapProps) {
  const total = clusters.reduce((acc, c) => acc + c.count, 0);
  const safeTotal = total > 0 ? total : 1;

  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-4",
        className,
      )}
    >
      <header className="flex items-center justify-between text-sm">
        <span className="font-semibold text-(--foreground-strong)">
          Memory clusters
        </span>
        <span className="text-xs tabular-nums text-(--muted-foreground)">
          {total.toLocaleString()} entries
        </span>
      </header>
      {/* stacked bar */}
      <div
        aria-label="cluster distribution"
        className="flex h-3 w-full overflow-hidden rounded-(--radius-pill) bg-(--surface)"
      >
        {clusters.map((c, i) => {
          const tone = c.tone ?? TONE_CYCLE[i % TONE_CYCLE.length];
          const pct = (c.count / safeTotal) * 100;
          return (
            <span
              key={c.id}
              className={cn("h-full", TONE_BG[tone])}
              style={{ width: `${pct}%` }}
              title={`${c.label} ${c.count}`}
            />
          );
        })}
      </div>
      <ul className="flex flex-col gap-1.5 text-sm">
        {clusters.map((c, i) => {
          const tone = c.tone ?? TONE_CYCLE[i % TONE_CYCLE.length];
          const pct = ((c.count / safeTotal) * 100).toFixed(1);
          return (
            <li
              key={c.id}
              className="flex items-center justify-between gap-3 border-b border-(--border) py-1.5 last:border-b-0"
            >
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className={cn(
                    "inline-block h-2 w-2 rounded-(--radius-pill)",
                    TONE_DOT[tone],
                  )}
                />
                <span className="text-(--foreground)">{c.label}</span>
                {c.keywords && c.keywords.length > 0 ? (
                  <span className="text-xs text-(--muted-foreground)">
                    {c.keywords.slice(0, 3).join(", ")}
                  </span>
                ) : null}
              </span>
              <span className="text-xs tabular-nums text-(--muted-foreground)">
                {c.count.toLocaleString()} · {pct}%
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
