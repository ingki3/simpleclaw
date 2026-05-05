"use client";

/**
 * Button — Atomic. admin.pen reusable: ButtonPrimary/Secondary/Ghost/Destructive 박제.
 *
 * variants: primary | secondary | ghost | destructive (admin.pen 4종 + outline 보조)
 * sizes:    sm | md | lg
 * states:   default / hover / active / focus / disabled / error 6종 표준 (DESIGN.md §3)
 *
 * 라이트/다크 분기는 토큰이 자동 swap — 컴포넌트 내부 분기 0줄.
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
  /** 좌측 아이콘 슬롯. */
  leftIcon?: ReactNode;
  /** 우측 아이콘 슬롯. */
  rightIcon?: ReactNode;
  /** 전체 너비를 채울지 여부. */
  fullWidth?: boolean;
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
  // §2.5 — 버튼 내부 padding [10, 16]
  sm: "px-3 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2.5 text-sm gap-2",
  lg: "px-5 py-3 text-base gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    className,
    leftIcon,
    rightIcon,
    fullWidth,
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
      data-variant={variant}
      data-size={size}
      className={cn(
        "inline-flex items-center justify-center rounded-(--radius-m) font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        VARIANT[variant],
        SIZE[size],
        fullWidth && "w-full",
        className,
      )}
      {...rest}
    >
      {leftIcon ? <span aria-hidden>{leftIcon}</span> : null}
      {children}
      {rightIcon ? <span aria-hidden>{rightIcon}</span> : null}
    </button>
  );
});
