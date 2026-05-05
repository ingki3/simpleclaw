"use client";

/**
 * Textarea — Atomic. 자동 높이 옵션 (DESIGN.md §3.1).
 *
 * `autoGrow` 가 true 일 때 입력 길이에 따라 높이 자동 조정 (min ~ max rows).
 */

import {
  forwardRef,
  useEffect,
  useRef,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/cn";

export interface TextareaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean;
  /** true 시 입력 길이에 따라 높이 자동 조정. */
  autoGrow?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea(
    { error, autoGrow, className, value, defaultValue, onChange, ...rest },
    ref,
  ) {
    const localRef = useRef<HTMLTextAreaElement | null>(null);

    // forwardRef 와 내부 ref 를 동시 보존.
    const setRef = (node: HTMLTextAreaElement | null) => {
      localRef.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) ref.current = node;
    };

    useEffect(() => {
      if (!autoGrow || !localRef.current) return;
      const el = localRef.current;
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }, [autoGrow, value]);

    return (
      <textarea
        ref={setRef}
        value={value}
        defaultValue={defaultValue}
        onChange={onChange}
        data-error={error || undefined}
        className={cn(
          "min-h-[80px] w-full rounded-(--radius-m) border bg-(--card) px-3 py-2 text-sm text-(--foreground) placeholder:text-(--placeholder) transition-colors focus:border-(--primary) focus:outline-none disabled:cursor-not-allowed disabled:opacity-60",
          error
            ? "border-(--color-error)"
            : "border-(--border-strong)",
          className,
        )}
        {...rest}
      />
    );
  },
);
