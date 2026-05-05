/**
 * DryRunCard — Molecular. Before/After diff + 적용 액션 (DESIGN.md §3.2, §4.3).
 *
 * Hot 변경 외에는 운영자가 적용 전에 영향 범위를 확인할 수 있어야 한다.
 * `impact` 슬롯은 보통 한 줄 한국어 ("최근 1시간 트래픽 중 12건이 새 임계치에서 차단됩니다").
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "../atoms/Button";

export interface DryRunCardProps {
  before: ReactNode;
  after: ReactNode;
  /** 영향 요약 한 줄 — 비어 있을 수 있다. */
  impact?: ReactNode;
  onApply?: () => void;
  onCancel?: () => void;
  applyLabel?: string;
  cancelLabel?: string;
  className?: string;
}

export function DryRunCard({
  before,
  after,
  impact,
  onApply,
  onCancel,
  applyLabel = "변경 적용",
  cancelLabel = "취소",
  className,
}: DryRunCardProps) {
  return (
    <section
      aria-label="dry-run preview"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-6",
        className,
      )}
    >
      <header className="text-sm font-medium text-(--foreground)">
        변경 미리보기 (Dry-run)
      </header>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-(--radius-m) border border-(--border) bg-(--surface) p-3 text-sm">
          <div className="mb-1 text-xs uppercase tracking-wide text-(--muted-foreground)">
            Before
          </div>
          <div>{before}</div>
        </div>
        <div className="rounded-(--radius-m) border border-(--primary) bg-(--primary-tint) p-3 text-sm text-(--foreground)">
          <div className="mb-1 text-xs uppercase tracking-wide text-(--primary)">
            After
          </div>
          <div>{after}</div>
        </div>
      </div>
      {impact ? (
        <p className="text-sm text-(--muted-foreground)">{impact}</p>
      ) : null}
      <footer className="flex justify-end gap-2">
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel}>
            {cancelLabel}
          </Button>
        ) : null}
        {onApply ? (
          <Button variant="primary" onClick={onApply}>
            {applyLabel}
          </Button>
        ) : null}
      </footer>
    </section>
  );
}
