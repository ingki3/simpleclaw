/**
 * MetricCard — Molecular. 라벨 + 큰 값 + delta + sparkline 슬롯 (DESIGN.md §3.2).
 *
 * Dashboard 의 가장 빈번한 카드. 값은 한 줄 큰 typography.
 * delta 는 +/- 부호 + tone (positive=success / negative=danger).
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface MetricCardProps {
  label: ReactNode;
  value: ReactNode;
  /** delta 값 — number 시 자동 부호/tone, string 시 부모가 직접 표기. */
  delta?: number | string;
  /** delta 가 string 일 때 명시 tone. number 일 때는 부호 기반 자동 결정. */
  deltaTone?: "positive" | "negative" | "neutral";
  /** sparkline 또는 부가 미니 그래프 슬롯. */
  sparkline?: ReactNode;
  className?: string;
}

function deriveTone(
  delta: MetricCardProps["delta"],
  deltaTone: MetricCardProps["deltaTone"],
): "positive" | "negative" | "neutral" {
  if (deltaTone) return deltaTone;
  if (typeof delta === "number") {
    if (delta > 0) return "positive";
    if (delta < 0) return "negative";
  }
  return "neutral";
}

const DELTA_CLASS: Record<"positive" | "negative" | "neutral", string> = {
  positive: "text-(--color-success)",
  negative: "text-(--color-error)",
  neutral: "text-(--muted-foreground)",
};

export function MetricCard({
  label,
  value,
  delta,
  deltaTone,
  sparkline,
  className,
}: MetricCardProps) {
  const tone = deriveTone(delta, deltaTone);
  const deltaText =
    typeof delta === "number"
      ? `${delta > 0 ? "+" : ""}${delta}`
      : delta;

  return (
    <article
      className={cn(
        "flex flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-6 shadow-(--shadow-sm)",
        className,
      )}
    >
      <div className="text-xs uppercase tracking-wide text-(--muted-foreground)">
        {label}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold text-(--foreground-strong)">
          {value}
        </span>
        {deltaText !== undefined && deltaText !== "" ? (
          <span className={cn("text-xs font-medium", DELTA_CLASS[tone])}>
            {deltaText}
          </span>
        ) : null}
      </div>
      {sparkline ? <div>{sparkline}</div> : null}
    </article>
  );
}
