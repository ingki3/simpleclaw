/**
 * HealthDot — Molecular. 4-tone dot + tooltip (DESIGN.md §3.2, §4.5).
 *
 * tones: green (정상) / amber (주의) / red (실패) / grey (알 수 없음).
 * 색만으로 의미를 전달하지 않도록 sr-only 라벨 또는 tooltip 을 항상 동반.
 */

import { cn } from "@/lib/cn";

export type HealthTone = "green" | "amber" | "red" | "grey";

const TONE: Record<HealthTone, string> = {
  green: "bg-(--color-success)",
  amber: "bg-(--color-warning)",
  red: "bg-(--color-error)",
  grey: "bg-(--muted-foreground)",
};

const LABEL: Record<HealthTone, string> = {
  green: "정상",
  amber: "주의",
  red: "실패",
  grey: "알 수 없음",
};

export interface HealthDotProps {
  tone: HealthTone;
  /** 시각 라벨 — 비워두면 sr-only 라벨로 폴백. */
  label?: string;
  /** pulse 애니메이션 — health flash (DESIGN.md §4.5). */
  pulse?: boolean;
  className?: string;
}

export function HealthDot({ tone, label, pulse, className }: HealthDotProps) {
  return (
    <span
      data-tone={tone}
      className={cn("inline-flex items-center gap-1.5 text-sm", className)}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-2 w-2 rounded-(--radius-pill)",
          TONE[tone],
          pulse && "animate-pulse",
        )}
      />
      {label !== undefined ? (
        <span>{label}</span>
      ) : (
        <span className="sr-only">{LABEL[tone]}</span>
      )}
    </span>
  );
}
