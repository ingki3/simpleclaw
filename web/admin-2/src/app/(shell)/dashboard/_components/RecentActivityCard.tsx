/**
 * RecentActivityCard — 최근 변경 (audit feed) 카드.
 *
 * AuditEntry molecule 를 그대로 적층 — Dashboard 가 Audit 영역의 최근 N건을
 * 미리보기로 띄운다. 카드 footer 의 "전체 보기" 링크로 /audit 으로 이동.
 */
import Link from "next/link";
import { AuditEntry, type AuditEntryProps } from "@/design/molecules/AuditEntry";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";

interface RecentActivityCardProps {
  entries: readonly AuditEntryProps[];
  className?: string;
}

export function RecentActivityCard({
  entries,
  className,
}: RecentActivityCardProps) {
  return (
    <section
      data-testid="recent-activity"
      aria-label="최근 변경"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-6 shadow-(--shadow-sm)",
        className,
      )}
    >
      <header className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-(--foreground-strong)">
          최근 변경
        </h2>
        <Link
          href="/audit"
          data-testid="recent-activity-view-all"
          className="text-xs font-medium text-(--primary) hover:underline"
        >
          전체 보기 →
        </Link>
      </header>

      {entries.length === 0 ? (
        <EmptyState
          title="아직 변경 이력이 없습니다"
          description="운영자 또는 에이전트의 모든 변경이 자동으로 여기에 기록됩니다."
        />
      ) : (
        <ul className="flex flex-col">
          {entries.map((e, i) => (
            <li key={e.traceId ?? `${e.actor}-${i}`}>
              <AuditEntry {...e} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
