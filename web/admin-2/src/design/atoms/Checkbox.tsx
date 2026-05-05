"use client";

/**
 * Checkbox — Atomic. 네이티브 <input type="checkbox"> 위에 토큰 스타일.
 *
 * 시각 마크는 CSS 만으로 구현 — 라이트/다크 분기 없음.
 */

import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export interface CheckboxProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  /** 우측 라벨 — 별도 <label> 없이 한 줄로 그릴 때 사용. */
  label?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  function Checkbox({ label, className, disabled, ...rest }, ref) {
    const input = (
      <input
        ref={ref}
        type="checkbox"
        disabled={disabled}
        className={cn(
          "h-4 w-4 shrink-0 cursor-pointer appearance-none rounded-(--radius-sm) border-2 border-(--border-strong) bg-(--card) transition-colors checked:border-(--primary) checked:bg-(--primary) disabled:cursor-not-allowed disabled:opacity-50",
          // 체크 마크 — checked 시 ::before SVG 대체로 토큰 색상 inline.
          "checked:bg-[url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22 fill=%22none%22><path d=%22M3 8l3 3 7-7%22 stroke=%22white%22 stroke-width=%222%22 stroke-linecap=%22round%22 stroke-linejoin=%22round%22/></svg>')] checked:bg-center checked:bg-no-repeat",
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
  },
);
