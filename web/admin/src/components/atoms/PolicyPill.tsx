/**
 * PolicyPill — DESIGN.md §3.2 PolicyChip / §1 원칙 4 (Hot/Restart 등급).
 *
 * 적용 등급:
 *  - hot:              즉시 적용. tone=success.
 *  - service-restart:  서비스 단위 재시작 필요. tone=warning.
 *  - process-restart:  데몬 프로세스 재시작 필요(가장 무거움). tone=danger.
 *
 * 시각 + 텍스트 + 아이콘 3중 표시 (DESIGN.md §1 원칙 5와 정렬).
 */

import { Bolt, RotateCcw, Power } from "lucide-react";
import { cn } from "@/lib/cn";

export type PolicyLevel = "hot" | "service-restart" | "process-restart";

const META: Record<
  PolicyLevel,
  { label: string; tone: string; Icon: typeof Bolt }
> = {
  hot: {
    label: "Hot",
    tone: "bg-[--color-success-bg] text-[--color-success]",
    Icon: Bolt,
  },
  "service-restart": {
    label: "Service restart",
    tone: "bg-[--color-warning-bg] text-[--color-warning]",
    Icon: RotateCcw,
  },
  "process-restart": {
    label: "Process restart",
    tone: "bg-[--color-error-bg] text-[--color-error]",
    Icon: Power,
  },
};

export function PolicyPill({
  level,
  className,
}: {
  level: PolicyLevel;
  className?: string;
}) {
  const { label, tone, Icon } = META[level];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[--radius-pill] px-2 py-0.5 text-xs font-medium",
        tone,
        className,
      )}
      title={`적용 등급: ${label}`}
    >
      <Icon size={12} aria-hidden />
      <span>{label}</span>
    </span>
  );
}
