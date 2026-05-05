/**
 * RecentAlertsCard — 최근 에러 / 알림 카드.
 *
 * AuditEntry 와 시각 구조는 비슷하지만, 이 카드는 *현재 살아있는 알림* 을 노출한다.
 * StatusPill tone 이 행 좌측에 와서 한눈에 우선순위를 파악할 수 있게 한다.
 * footer 의 "전체 보기" 는 /logging (trace timeline) 으로 이동.
 */
import Link from "next/link";
import { StatusPill } from "@/design/atoms/StatusPill";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { DashboardAlert } from "../_data";

interface RecentAlertsCardProps {
  alerts: readonly DashboardAlert[];
  className?: string;
}

export function RecentAlertsCard({ alerts, className }: RecentAlertsCardProps) {
  return (
    <section
      data-testid="recent-alerts"
      aria-label="최근 에러 및 알림"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-6 shadow-(--shadow-sm)",
        className,
      )}
    >
      <header className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-(--foreground-strong)">
          최근 에러 / 알림
        </h2>
        <Link
          href="/logging"
          data-testid="recent-alerts-view-all"
          className="text-xs font-medium text-(--primary) hover:underline"
        >
          전체 보기 →
        </Link>
      </header>

      {alerts.length === 0 ? (
        <EmptyState
          title="발생한 알림이 없습니다"
          description="모든 도메인이 정상 — 새 이상 신호가 발생하면 즉시 노출됩니다."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {alerts.map((a) => (
            <li
              key={a.id}
              data-testid={`alert-${a.id}`}
              className="flex flex-col gap-1 rounded-(--radius-m) border border-(--border) bg-(--surface) p-3"
            >
              <div className="flex items-center gap-2">
                <StatusPill tone={a.tone}>{toneLabel(a.tone)}</StatusPill>
                <span className="truncate text-sm font-medium text-(--foreground-strong)">
                  {a.headline}
                </span>
                <span className="ml-auto shrink-0 text-xs text-(--muted-foreground)">
                  {a.timestamp}
                </span>
              </div>
              <p className="text-xs text-(--muted-foreground)">{a.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** StatusPill 의 tone 만으로는 의미가 약하므로 우리말 라벨을 동반. */
function toneLabel(tone: DashboardAlert["tone"]): string {
  switch (tone) {
    case "success":
      return "정상";
    case "warning":
      return "주의";
    case "error":
      return "실패";
    case "info":
      return "정보";
    case "neutral":
    default:
      return "기타";
  }
}
