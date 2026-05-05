/**
 * Label — Atomic. 폼 라벨 (DESIGN.md §3.1).
 *
 * required/optional 마커 슬롯 + hint 슬롯. <label htmlFor> 매칭 강제.
 */

import type { LabelHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface LabelProps extends LabelHTMLAttributes<HTMLLabelElement> {
  required?: boolean;
  optional?: boolean;
  /** 라벨 우측의 도움말 슬롯 — Tooltip 트리거 등. */
  hint?: ReactNode;
}

export function Label({
  required,
  optional,
  hint,
  className,
  children,
  ...rest
}: LabelProps) {
  return (
    <label
      className={cn(
        "inline-flex items-center gap-1.5 text-sm font-medium text-(--foreground)",
        className,
      )}
      {...rest}
    >
      <span>{children}</span>
      {required ? (
        <span aria-hidden className="text-(--color-error)">
          *
        </span>
      ) : null}
      {optional ? (
        <span className="text-xs font-normal text-(--muted-foreground)">
          (선택)
        </span>
      ) : null}
      {hint ? <span className="ml-1">{hint}</span> : null}
    </label>
  );
}
