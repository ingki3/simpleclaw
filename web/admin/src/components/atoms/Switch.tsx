"use client";

/**
 * Switch (Toggle) — DESIGN.md §3.1 + §5 a11y(role=switch + aria-checked).
 *
 * 시각: pill 트랙 + thumb. checked일 때 트랙은 brand, 미체크 시 border-strong.
 * 비제어 입력도 허용하지만 본 디자인 시스템에선 controlled를 권장한다.
 */

import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  /** 스크린리더용 라벨 — 시각 라벨이 별도로 있다면 aria-labelledby로 대체 가능. */
  label?: string;
}

export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked, onCheckedChange, label, disabled, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 items-center rounded-[--radius-pill] border transition-colors",
        checked
          ? "bg-[--primary] border-transparent"
          : "bg-[--card] border-[--border-strong]",
        disabled && "cursor-not-allowed opacity-50",
        className,
      )}
      {...rest}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-4 w-4 transform rounded-[--radius-pill] bg-white shadow-[--shadow-sm] transition-transform",
          checked ? "translate-x-5" : "translate-x-1",
        )}
      />
    </button>
  );
});
