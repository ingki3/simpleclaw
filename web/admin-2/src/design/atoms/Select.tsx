"use client";

/**
 * Select — Atomic. 네이티브 <select> 기반 (DESIGN.md §3.1).
 *
 * Combobox 변형 (검색 가능 옵션) 은 S3 이후 도메인 화면에서 필요해질 때 별도 추가.
 * 본 컴포넌트는 가장 자주 쓰이는 단일 선택 드롭다운만 박제한다.
 */

import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  options: SelectOption[];
  error?: boolean;
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { options, error, placeholder, className, disabled, value, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      disabled={disabled}
      value={value}
      data-error={error || undefined}
      className={cn(
        "h-10 w-full appearance-none rounded-(--radius-m) border bg-(--card) px-3 pr-8 text-sm text-(--foreground) transition-colors focus:border-(--primary) focus:outline-none disabled:cursor-not-allowed disabled:opacity-60",
        error
          ? "border-(--color-error)"
          : "border-(--border-strong)",
        className,
      )}
      {...rest}
    >
      {placeholder ? (
        <option value="" disabled>
          {placeholder}
        </option>
      ) : null}
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
});
