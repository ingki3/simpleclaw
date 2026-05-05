/**
 * ProviderCard — admin.pen `BBA7M` 의 프로바이더 카드 한 장 (DESIGN.md §3.2 / §4.1).
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — 이름 + default 뱃지 + 카드 우상단 Edit 버튼
 *  2) model / base_url 메타 행
 *  3) SecretField — apiKeyMasked + reveal/copy/rotate
 *  4) StatusPill + 한 줄 헬스 라벨 + (선택) 보조 detail
 *  5) MetricCard 미니 행 — 평균 지연 + 24h 토큰
 *
 * 카드 자체는 클릭 안 됨 (Edit 버튼만 트리거). default 인 경우 좌측 액센트 보더.
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { SecretField } from "@/design/atoms/SecretField";
import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { RouterProvider } from "../_data";

interface ProviderCardProps {
  provider: RouterProvider;
  /** Edit 버튼 클릭 시 — 부모가 EditProviderModal 을 연다. */
  onEdit: (provider: RouterProvider) => void;
  className?: string;
}

export function ProviderCard({ provider, onEdit, className }: ProviderCardProps) {
  const { health } = provider;
  return (
    <article
      data-testid={`provider-card-${provider.id}`}
      data-default={provider.isDefault || undefined}
      data-health={health.tone}
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border bg-(--card) p-5 shadow-(--shadow-sm)",
        provider.isDefault
          ? "border-(--primary) ring-1 ring-(--primary-tint)"
          : "border-(--border)",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-semibold text-(--foreground-strong)">
            {provider.name}
          </h3>
          {provider.isDefault ? (
            <Badge tone="brand" data-testid={`provider-card-${provider.id}-default`}>
              default
            </Badge>
          ) : null}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onEdit(provider)}
          data-testid={`provider-card-${provider.id}-edit`}
        >
          편집
        </Button>
      </header>

      <dl className="flex flex-col gap-1 text-xs text-(--muted-foreground)">
        <div className="flex items-baseline gap-2">
          <dt className="w-16 shrink-0 uppercase tracking-wide">model</dt>
          <dd className="font-mono text-(--foreground)">{provider.model}</dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="w-16 shrink-0 uppercase tracking-wide">api</dt>
          <dd className="font-mono text-(--foreground)">{provider.baseUrl}</dd>
        </div>
      </dl>

      <SecretField
        maskedPreview={provider.apiKeyMasked}
        onReveal={() => {
          /* 본 단계 mock — 실제 fetch 는 데몬 통합 단계에서. */
        }}
        onCopy={() => {
          if (typeof navigator !== "undefined" && navigator.clipboard) {
            void navigator.clipboard.writeText(provider.apiKeyMasked);
          }
        }}
        onRotate={() => onEdit(provider)}
      />

      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <StatusPill tone={health.tone}>{health.label}</StatusPill>
        </div>
        {health.detail ? (
          <p className="text-xs text-(--muted-foreground)">{health.detail}</p>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-2 border-t border-(--border) pt-3">
        <MiniMetric
          label="avg latency"
          value={
            health.avgLatencyMs > 0 ? `${health.avgLatencyMs}ms` : "—"
          }
        />
        <MiniMetric label="24h tokens" value={health.tokens24h} />
      </div>
    </article>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-(--muted-foreground)">
        {label}
      </span>
      <span className="font-mono text-sm font-semibold text-(--foreground-strong)">
        {value}
      </span>
    </div>
  );
}
