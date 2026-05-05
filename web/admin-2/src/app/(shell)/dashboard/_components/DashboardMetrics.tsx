/**
 * DashboardMetrics — Dashboard 4-카드 metric 띠 (admin.pen `xNjlT` content row).
 *
 * 4개의 MetricCard 를 grid 로 정렬. caption 은 카드 내부 보조 라인으로 노출 —
 * sparkline 대신 후속 sub-issue 가 채울 수 있는 슬롯이다.
 */
import { MetricCard } from "@/design/molecules/MetricCard";
import { cn } from "@/lib/cn";
import type { DashboardMetric } from "../_data";

interface DashboardMetricsProps {
  metrics: readonly DashboardMetric[];
  className?: string;
}

export function DashboardMetrics({ metrics, className }: DashboardMetricsProps) {
  return (
    <section
      aria-label="운영 지표"
      data-testid="dashboard-metrics"
      className={cn(
        "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4",
        className,
      )}
    >
      {metrics.map((m) => (
        <div key={m.key} data-testid={`metric-${m.key}`}>
          <MetricCard
            label={m.label}
            value={m.value}
            delta={m.delta}
            deltaTone={m.deltaTone}
            sparkline={
              m.caption ? (
                <p
                  data-testid={`metric-caption-${m.key}`}
                  className="text-xs text-(--muted-foreground)"
                >
                  {m.caption}
                </p>
              ) : null
            }
          />
        </div>
      ))}
    </section>
  );
}
