/**
 * SubAgentPoolCard — admin.pen `lQQaY` (cardSubAgentPool) 박제.
 *
 * 3 행 메타 + 1 행 hint:
 *  - Pool 크기 (4 / 8)
 *  - Idle / Active (3 idle · 1 active)
 *  - Wait state (Badge: dreaming / waiting / running)
 *  - 다음 dreaming cycle 까지의 시간 (BIZ-66 hint)
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { cn } from "@/lib/cn";
import type { SubAgentPoolInfo } from "../_data";

interface SubAgentPoolCardProps {
  pool: SubAgentPoolInfo;
  className?: string;
}

export function SubAgentPoolCard({ pool, className }: SubAgentPoolCardProps) {
  return (
    <section
      data-testid="sub-agent-pool-card"
      aria-label="Sub-agent Pool · Dreaming"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">
        Sub-agent Pool · Dreaming
      </h2>

      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-(--muted-foreground)">Pool 크기</span>
        <span
          data-testid="sub-agent-pool-usage"
          className="font-mono text-(--foreground)"
        >
          {pool.poolUsage}
        </span>
      </div>

      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-(--muted-foreground)">Idle / Active</span>
        <span className="font-mono text-(--foreground)">
          {pool.idleActiveSummary}
        </span>
      </div>

      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-(--muted-foreground)">Wait state</span>
        <Badge tone="info" data-testid="sub-agent-pool-wait-state">
          {pool.waitState}
        </Badge>
      </div>

      <p className="text-[11px] text-(--muted-foreground)">
        {pool.nextDreamingHint}
      </p>
    </section>
  );
}
