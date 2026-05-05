/**
 * FormRow — Molecular. 가로 2분할: 좌측 name(라벨/도움말), 우측 value(입력) (DESIGN.md §3.2, §4.1).
 *
 * 페이지 섹션 카드 안에서 한 항목씩 쌓이는 형태가 표준.
 * 모바일 (sm 이하) 에서는 세로 1열로 자동 폴백.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface FormRowProps {
  /** 좌측 영역 — 라벨, 도움말, PolicyChip 등. */
  name: ReactNode;
  /** 우측 영역 — 입력 컨트롤, 액션 버튼. */
  value: ReactNode;
  className?: string;
}

export function FormRow({ name, value, className }: FormRowProps) {
  return (
    <div
      className={cn(
        "grid gap-3 py-3 sm:grid-cols-[260px_1fr] sm:gap-6",
        className,
      )}
    >
      <div className="text-sm text-(--foreground)">{name}</div>
      <div>{value}</div>
    </div>
  );
}
