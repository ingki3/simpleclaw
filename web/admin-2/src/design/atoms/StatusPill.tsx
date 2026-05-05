/**
 * StatusPill — Atomic. dot + 라벨 (DESIGN.md §3.1, §5).
 *
 * 색만으로 상태를 표현하지 않는다 — dot 은 시각적 재확인용이고 라벨이 SSOT.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type StatusTone = "success" | "warning" | "error" | "info" | "neutral";

const DOT: Record<StatusTone, string> = {
  success: "bg-(--color-success)",
  warning: "bg-(--color-warning)",
  error: "bg-(--color-error)",
  info: "bg-(--color-info)",
  neutral: "bg-(--muted-foreground)",
};

const TONE_BG: Record<StatusTone, string> = {
  success: "bg-(--color-success-bg) text-(--color-success)",
  warning: "bg-(--color-warning-bg) text-(--color-warning)",
  error: "bg-(--color-error-bg) text-(--color-error)",
  info: "bg-(--color-info-bg) text-(--color-info)",
  neutral: "bg-(--surface) text-(--muted-foreground)",
};

export interface StatusPillProps {
  tone: StatusTone;
  children: ReactNode;
  className?: string;
  /** dot 만 보이고 텍스트는 sr-only — 드물게 사용. */
  iconOnly?: boolean;
}

export function StatusPill({
  tone,
  children,
  className,
  iconOnly,
}: StatusPillProps) {
  return (
    <span
      data-tone={tone}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-(--radius-pill) px-2 py-0.5 text-xs font-medium",
        TONE_BG[tone],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-(--radius-pill)",
          DOT[tone],
        )}
      />
      {iconOnly ? (
        <span className="sr-only">{children}</span>
      ) : (
        <span>{children}</span>
      )}
    </span>
  );
}
