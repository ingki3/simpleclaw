/**
 * InputGroup — Molecular. Label + Input + Hint + Error 한 묶음 (DESIGN.md §3.2).
 *
 * `htmlFor` / `id` 매칭은 외부에서 직접 부여한다 — 본 묶음은 시각 그룹핑만 담당.
 * Setting Edit Pattern (DESIGN.md §4.1) 의 가장 흔한 구성 단위.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface InputGroupProps {
  label: ReactNode;
  /** 입력 컨트롤 (Input/Textarea/Select 등). */
  children: ReactNode;
  hint?: ReactNode;
  /** 에러 텍스트 — truthy 면 hint 자리를 대체. */
  error?: ReactNode;
  required?: boolean;
  className?: string;
}

export function InputGroup({
  label,
  children,
  hint,
  error,
  required,
  className,
}: InputGroupProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <span className="inline-flex items-center gap-1 text-sm font-medium text-(--foreground)">
        {label}
        {required ? (
          <span aria-hidden className="text-(--color-error)">
            *
          </span>
        ) : null}
      </span>
      {children}
      {error ? (
        <span role="alert" className="text-xs text-(--color-error)">
          {error}
        </span>
      ) : hint ? (
        <span className="text-xs text-(--muted-foreground)">{hint}</span>
      ) : null}
    </div>
  );
}
