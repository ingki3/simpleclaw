/**
 * FallbackChainCard — admin.pen `BBA7M` 의 "Fallback chain" 카드.
 *
 * 1순위 → 2순위 → 3순위 pill 행 + "추가" 버튼.
 * 본 단계는 시각 박제만 — 드래그 재정렬은 후속 sub-issue 가 추가.
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { cn } from "@/lib/cn";
import type { RouterProvider } from "../_data";

interface FallbackChainCardProps {
  chain: readonly string[];
  providers: readonly RouterProvider[];
  onAdd: () => void;
  className?: string;
}

export function FallbackChainCard({
  chain,
  providers,
  onAdd,
  className,
}: FallbackChainCardProps) {
  // chain 의 id 를 provider 객체로 매핑 — 알 수 없는 id 는 자동 무시.
  const items = chain
    .map((id) => providers.find((p) => p.id === id))
    .filter((p): p is RouterProvider => Boolean(p));

  return (
    <section
      data-testid="fallback-chain"
      aria-label="Fallback chain"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <header className="flex flex-col gap-1">
        <h2 className="text-base font-semibold text-(--foreground-strong)">
          Fallback chain
        </h2>
        <p className="text-xs text-(--muted-foreground)">
          기본 라우터가 timeout/error 일 때 순서대로 시도합니다. 드래그로 순서 변경 (예정).
        </p>
      </header>
      <div className="flex flex-wrap items-center gap-2">
        {items.length === 0 ? (
          <p
            data-testid="fallback-chain-empty"
            className="text-sm text-(--muted-foreground)"
          >
            체인이 비어 있습니다 — 기본 프로바이더만 사용됩니다.
          </p>
        ) : (
          items.map((provider, idx) => (
            <span
              key={provider.id}
              data-testid={`fallback-chain-item-${provider.id}`}
              className="inline-flex items-center gap-2 rounded-(--radius-pill) border border-(--border-strong) bg-(--surface) px-3 py-1 text-xs font-medium text-(--foreground)"
            >
              <span className="font-mono text-(--muted-foreground)">
                {idx + 1}
              </span>
              <span aria-hidden>·</span>
              <span>{provider.name}</span>
            </span>
          ))
        )}
        <Button
          size="sm"
          variant="ghost"
          onClick={onAdd}
          data-testid="fallback-chain-add"
        >
          ＋ 추가
        </Button>
      </div>
    </section>
  );
}
