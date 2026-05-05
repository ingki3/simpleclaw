/**
 * SystemStatusRow — Dashboard 상단 4도메인 헬스 띠 (admin.pen `xNjlT`).
 *
 * Daemon · LLM · Webhook · Cron 4영역의 한 줄 상태를 노출. 각 도메인은 클릭 시
 * 해당 영역 라우트로 이동 — 운영자가 헬스 → 영역 점프를 빠르게 할 수 있게 한다.
 *
 * 색만으로 의미를 전달하지 않는다 — HealthDot 의 sr-only 라벨 + 가시 caption 동반.
 */
import Link from "next/link";
import { HealthDot } from "@/design/molecules/HealthDot";
import { cn } from "@/lib/cn";
import type { DomainHealth } from "../_data";

interface SystemStatusRowProps {
  domains: readonly DomainHealth[];
  className?: string;
}

/** 도메인 키 → 라우트 path. Sidebar 의 SSOT 와 동일한 영역 식별. */
const DOMAIN_HREF: Record<DomainHealth["key"], string> = {
  daemon: "/system",
  llm: "/llm-router",
  webhook: "/channels",
  cron: "/cron",
};

export function SystemStatusRow({ domains, className }: SystemStatusRowProps) {
  return (
    <ul
      data-testid="system-status-row"
      aria-label="시스템 도메인 상태"
      className={cn(
        "flex flex-wrap items-center gap-2 text-xs",
        className,
      )}
    >
      {domains.map((d) => (
        <li key={d.key}>
          <Link
            href={DOMAIN_HREF[d.key]}
            data-testid={`system-status-${d.key}`}
            data-tone={d.tone}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-(--radius-pill) border border-(--border) bg-(--card) px-2.5 py-1 font-medium text-(--foreground) transition-colors",
              "hover:border-(--border-strong) hover:bg-(--surface)",
            )}
            title={d.caption}
          >
            <HealthDot tone={d.tone} label={d.label} />
            <span className="sr-only">— {d.caption}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
