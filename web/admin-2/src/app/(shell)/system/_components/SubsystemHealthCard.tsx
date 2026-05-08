/**
 * SubsystemHealthCard — admin.pen `Z5UMJw` (cardHealth) 박제.
 *
 * 4행 (데몬 · Memory · Webhook · Cron) 의 헬스 dot + label + 짧은 detail.
 * 색만으로 상태를 전달하지 않도록 StatusPill 의 dot + 텍스트 라벨을 동반한다.
 */
"use client";

import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { SubsystemHealth } from "../_data";

interface SubsystemHealthCardProps {
  items: readonly SubsystemHealth[];
  className?: string;
}

const TONE_TEXT: Record<SubsystemHealth["tone"], string> = {
  success: "text-(--muted-foreground)",
  warning: "text-(--color-warning)",
  error: "text-(--color-error)",
  info: "text-(--color-info)",
  neutral: "text-(--muted-foreground)",
};

export function SubsystemHealthCard({ items, className }: SubsystemHealthCardProps) {
  return (
    <section
      data-testid="subsystem-health-card"
      aria-label="서브시스템 헬스"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">
        서브시스템 헬스
      </h2>
      <ul className="flex flex-col gap-2">
        {items.map((it) => (
          <li
            key={it.key}
            data-testid={`subsystem-health-${it.key}`}
            data-tone={it.tone}
            className="flex items-center justify-between gap-3 text-xs"
          >
            <StatusPill tone={it.tone}>{it.label}</StatusPill>
            <span className={cn("font-mono", TONE_TEXT[it.tone])}>
              {it.detail}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
