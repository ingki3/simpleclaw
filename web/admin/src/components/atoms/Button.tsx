"use client";

/**
 * Button — DESIGN.md §3.1 Atomic.
 *
 * variants: primary | secondary | outline | ghost | destructive
 * sizes:    sm | md | lg
 *
 * 시각 토큰만 사용하며, 라이트/다크 분기는 0줄 (토큰이 자동 swap).
 * disabled는 opacity로만 표현 — 색상 의미를 유지하되 인터랙션 차단.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "outline"
  | "ghost"
  | "destructive";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 좌측 슬롯 — 보통 lucide 아이콘. */
  leftIcon?: ReactNode;
  /** 우측 슬롯. */
  rightIcon?: ReactNode;
}

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-(--primary) text-(--primary-foreground) hover:bg-(--primary-hover)",
  secondary: "bg-(--card) text-(--foreground) hover:bg-(--surface)",
  outline:
    "border border-(--border-strong) bg-transparent text-(--foreground) hover:bg-(--surface)",
  ghost: "bg-transparent text-(--foreground) hover:bg-(--surface)",
  destructive:
    "bg-(--destructive) text-(--destructive-foreground) hover:opacity-90",
};

const SIZE: Record<ButtonSize, string> = {
  // §2.5 — 버튼 내부 padding [10, 16] / 입력은 [8, 12]
  sm: "px-3 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2.5 text-sm gap-2",
  lg: "px-5 py-3 text-base gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "primary",
      size = "md",
      className,
      leftIcon,
      rightIcon,
      children,
      type = "button",
      ...rest
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        className={cn(
          "inline-flex items-center justify-center rounded-(--radius-m) font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
          VARIANT[variant],
          SIZE[size],
          className,
        )}
        {...rest}
      >
        {leftIcon ? <span aria-hidden>{leftIcon}</span> : null}
        {children}
        {rightIcon ? <span aria-hidden>{rightIcon}</span> : null}
      </button>
    );
  },
);
