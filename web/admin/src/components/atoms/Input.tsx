"use client";

/**
 * Input — DESIGN.md §2.5 입력 padding [8,12] + §3.1 trailing/leading 슬롯.
 *
 * leading/trailing 슬롯이 없을 때는 표준 `<input>`과 시각적으로 동일하지만,
 * 슬롯을 받으면 wrapper로 감싸 padding을 좌/우로 흡수해 시각 정렬을 유지한다.
 * error 상태는 boolean 한 개로 받아 boundary 색을 danger로 swap한다.
 */

import { forwardRef, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  /** 검증 실패 시 true — boundary를 danger로 강조한다. */
  invalid?: boolean;
  /** 입력 셀 전체 폭 클래스. */
  containerClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { leftIcon, rightIcon, invalid, className, containerClassName, ...rest },
  ref,
) {
  const ringClass = invalid
    ? "border-[--color-error]"
    : "border-[--border] focus-within:border-[--primary]";

  if (!leftIcon && !rightIcon) {
    return (
      <input
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(
          "w-full rounded-[--radius-m] border bg-[--card] px-3 py-2 text-sm text-[--foreground] placeholder:text-[--placeholder] outline-none transition-colors",
          ringClass,
          className,
        )}
        {...rest}
      />
    );
  }

  return (
    <div
      className={cn(
        "flex w-full items-center gap-2 rounded-[--radius-m] border bg-[--card] px-3 py-2 transition-colors",
        ringClass,
        containerClassName,
      )}
    >
      {leftIcon ? (
        <span aria-hidden className="text-[--muted-foreground]">
          {leftIcon}
        </span>
      ) : null}
      <input
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(
          "min-w-0 flex-1 bg-transparent text-sm text-[--foreground] placeholder:text-[--placeholder] outline-none",
          className,
        )}
        {...rest}
      />
      {rightIcon ? (
        <span aria-hidden className="text-[--muted-foreground]">
          {rightIcon}
        </span>
      ) : null}
    </div>
  );
});
