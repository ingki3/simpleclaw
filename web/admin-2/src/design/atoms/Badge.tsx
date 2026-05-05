/**
 * Badge — Atomic. tone × size 변형 (DESIGN.md §3.1).
 *
 * tones: neutral | success | warning | danger | info | brand.
 */

import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export type BadgeTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "brand";
export type BadgeSize = "sm" | "md";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  size?: BadgeSize;
  children: ReactNode;
}

const TONE: Record<BadgeTone, string> = {
  neutral: "bg-(--surface) text-(--muted-foreground)",
  success: "bg-(--color-success-bg) text-(--color-success)",
  warning: "bg-(--color-warning-bg) text-(--color-warning)",
  danger: "bg-(--color-error-bg) text-(--color-error)",
  info: "bg-(--color-info-bg) text-(--color-info)",
  brand: "bg-(--primary-tint) text-(--primary)",
};

const SIZE: Record<BadgeSize, string> = {
  sm: "px-1.5 py-0.5 text-xs",
  md: "px-2 py-0.5 text-sm",
};

export function Badge({
  tone = "neutral",
  size = "sm",
  children,
  className,
  ...rest
}: BadgeProps) {
  return (
    <span
      data-tone={tone}
      className={cn(
        "inline-flex items-center rounded-(--radius-sm) font-medium",
        TONE[tone],
        SIZE[size],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
