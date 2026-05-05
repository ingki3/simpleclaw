"use client";

/**
 * IconButton — Atomic. 아이콘 단독 버튼 (DESIGN.md §3.1).
 *
 * 시각: 사각/원형 토글 가능. aria-label 필수.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export type IconButtonVariant = "default" | "ghost" | "primary";
export type IconButtonSize = "sm" | "md";
export type IconButtonShape = "square" | "round";

export interface IconButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  shape?: IconButtonShape;
  /** 스크린리더용 라벨 (필수) — 아이콘만으로 의미를 전달하지 않는다. */
  "aria-label": string;
  icon: ReactNode;
}

const VARIANT: Record<IconButtonVariant, string> = {
  default:
    "border border-(--border-strong) bg-(--card) text-(--foreground) hover:bg-(--surface)",
  ghost: "bg-transparent text-(--foreground) hover:bg-(--surface)",
  primary:
    "bg-(--primary) text-(--primary-foreground) hover:bg-(--primary-hover)",
};

const SIZE: Record<IconButtonSize, string> = {
  sm: "h-8 w-8 text-sm",
  md: "h-10 w-10 text-base",
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    {
      variant = "default",
      size = "md",
      shape = "square",
      className,
      icon,
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
          "inline-flex items-center justify-center transition-colors disabled:cursor-not-allowed disabled:opacity-50",
          shape === "round"
            ? "rounded-(--radius-pill)"
            : "rounded-(--radius-m)",
          VARIANT[variant],
          SIZE[size],
          className,
        )}
        {...rest}
      >
        <span aria-hidden>{icon}</span>
      </button>
    );
  },
);
