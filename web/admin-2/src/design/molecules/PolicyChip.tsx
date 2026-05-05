/**
 * PolicyChip — Molecular. Hot / Service-restart / Process-restart (DESIGN.md §1, §2.1, §3.2).
 *
 * 변경 적용 정책을 한 칩으로 표시 — Setting Edit Pattern 의 PolicyChip 슬롯.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type PolicyKind = "hot" | "service-restart" | "process-restart";

const POLICY: Record<
  PolicyKind,
  { label: string; tone: string; tooltip: string }
> = {
  hot: {
    label: "Hot",
    tone: "bg-(--color-success-bg) text-(--color-success)",
    tooltip: "즉시 반영 — 재시작 불필요",
  },
  "service-restart": {
    label: "Service-restart",
    tone: "bg-(--color-warning-bg) text-(--color-warning)",
    tooltip: "서비스 재시작 필요",
  },
  "process-restart": {
    label: "Process-restart",
    tone: "bg-(--color-error-bg) text-(--color-error)",
    tooltip: "프로세스 재시작 필요",
  },
};

export interface PolicyChipProps {
  kind: PolicyKind;
  /** 라벨 우측에 추가 메타 텍스트(예: ETA). */
  meta?: ReactNode;
  className?: string;
}

export function PolicyChip({ kind, meta, className }: PolicyChipProps) {
  const cfg = POLICY[kind];
  return (
    <span
      data-policy={kind}
      title={cfg.tooltip}
      className={cn(
        "inline-flex items-center gap-1 rounded-(--radius-sm) px-2 py-0.5 text-xs font-medium",
        cfg.tone,
        className,
      )}
    >
      <span>{cfg.label}</span>
      {meta ? (
        <span className="text-(--muted-foreground)">{meta}</span>
      ) : null}
    </span>
  );
}
