/**
 * EmptyState — Molecular. 일러스트(추후) + 본문 + CTA (DESIGN.md §3.2, §4.6).
 *
 * 첫 진입(빈 화면) 상태에서 다음 행동을 안내한다.
 * 일러스트는 S2 이후 정해질 예정이므로 본 컴포넌트에서는 슬롯만 노출.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface EmptyStateProps {
  title: ReactNode;
  description?: ReactNode;
  /** 일러스트/아이콘 슬롯 — 64px 권장. */
  illustration?: ReactNode;
  /** CTA 버튼 등. */
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  title,
  description,
  illustration,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-(--radius-l) border border-dashed border-(--border) bg-(--surface) p-12 text-center",
        className,
      )}
    >
      {illustration ? (
        <div aria-hidden className="text-(--muted-foreground)">
          {illustration}
        </div>
      ) : null}
      <h3 className="text-lg font-semibold text-(--foreground-strong)">
        {title}
      </h3>
      {description ? (
        <p className="max-w-md text-sm text-(--muted-foreground)">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
