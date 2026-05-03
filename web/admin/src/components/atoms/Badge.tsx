/**
 * Badge / Tag — DESIGN.md §3.1 / §2.6 (radius-sm).
 *
 * tone: neutral | success | warning | danger | info | brand.
 * 아이콘 슬롯은 의도적으로 두지 않는다 (의미 표현은 텍스트 라벨로만).
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type BadgeTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "brand";

const TONE: Record<BadgeTone, string> = {
  neutral: "bg-[--surface] text-[--muted-foreground] border-[--border-divider]",
  success: "bg-[--color-success-bg] text-[--color-success] border-transparent",
  warning: "bg-[--color-warning-bg] text-[--color-warning] border-transparent",
  danger: "bg-[--color-error-bg] text-[--color-error] border-transparent",
  info: "bg-[--color-info-bg] text-[--color-info] border-transparent",
  brand: "bg-[--primary-tint] text-[--primary] border-transparent",
};

export interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}

export function Badge({ tone = "neutral", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-[--radius-sm] border px-2 py-0.5 text-xs font-medium",
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
