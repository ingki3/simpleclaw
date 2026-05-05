"use client";

/**
 * Radio — Atomic. 네이티브 <input type="radio"> 위에 토큰 스타일.
 *
 * group 사용 시 동일 `name` 으로 묶고, 외부에서 controlled 로 제어.
 */

import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export interface RadioProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

export const Radio = forwardRef<HTMLInputElement, RadioProps>(function Radio(
  { label, className, disabled, ...rest },
  ref,
) {
  const input = (
    <input
      ref={ref}
      type="radio"
      disabled={disabled}
      className={cn(
        "h-4 w-4 shrink-0 cursor-pointer appearance-none rounded-(--radius-pill) border-2 border-(--border-strong) bg-(--card) transition-colors checked:border-(--primary) disabled:cursor-not-allowed disabled:opacity-50",
        // 내부 점 — radial-gradient 로 토큰 색 표현.
        "checked:bg-[radial-gradient(circle,var(--primary)_45%,transparent_50%)]",
        className,
      )}
      {...rest}
    />
  );
  if (!label) return input;
  return (
    <label
      className={cn(
        "inline-flex items-center gap-2 text-sm text-(--foreground)",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      {input}
      <span>{label}</span>
    </label>
  );
});
