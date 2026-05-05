"use client";

/**
 * Input — Atomic. text/number/password (DESIGN.md §3.1).
 *
 * 슬롯: leading/trailing — 아이콘 또는 prefix/suffix 텍스트.
 * 에러 상태는 `error` prop 으로 트리거 (DESIGN.md §3 표준 6-state 중 error).
 */

import { forwardRef, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  leading?: ReactNode;
  trailing?: ReactNode;
  error?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { leading, trailing, error, className, disabled, ...rest },
  ref,
) {
  return (
    <span
      data-error={error || undefined}
      className={cn(
        "inline-flex h-10 w-full items-center gap-2 rounded-(--radius-m) border bg-(--card) px-3 text-sm transition-colors focus-within:border-(--primary)",
        error
          ? "border-(--color-error)"
          : "border-(--border-strong)",
        disabled && "cursor-not-allowed opacity-60",
        className,
      )}
    >
      {leading ? (
        <span className="shrink-0 text-(--muted-foreground)" aria-hidden>
          {leading}
        </span>
      ) : null}
      <input
        ref={ref}
        disabled={disabled}
        className="min-w-0 flex-1 bg-transparent text-(--foreground) placeholder:text-(--placeholder) focus:outline-none disabled:cursor-not-allowed"
        {...rest}
      />
      {trailing ? (
        <span className="shrink-0 text-(--muted-foreground)" aria-hidden>
          {trailing}
        </span>
      ) : null}
    </span>
  );
});
