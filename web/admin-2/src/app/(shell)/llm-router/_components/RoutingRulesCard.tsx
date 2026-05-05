/**
 * RoutingRulesCard — 라우팅 규칙 목록 + Add/Edit 트리거.
 *
 * admin.pen `Sms7l` (Routing Rule Editor) 의 진입점이 본 카드.
 * 본 단계는 1 규칙 fixture 로 시각 박제만 하고, 룰 편집/추가/삭제 흐름은 modal 에서 진행.
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { RoutingRule } from "../_data";

interface RoutingRulesCardProps {
  rules: readonly RoutingRule[];
  onAdd: () => void;
  onEdit: (rule: RoutingRule) => void;
  className?: string;
}

export function RoutingRulesCard({
  rules,
  onAdd,
  onEdit,
  className,
}: RoutingRulesCardProps) {
  return (
    <section
      data-testid="routing-rules"
      aria-label="라우팅 규칙"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <header className="flex items-baseline justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-semibold text-(--foreground-strong)">
            라우팅 규칙
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            트리거 조건에 따라 다른 모델/우선순위로 라우팅합니다 (DESIGN.md §4.3).
          </p>
        </div>
        <Button
          size="sm"
          variant="primary"
          onClick={onAdd}
          data-testid="routing-rules-add"
        >
          ＋ 규칙 추가
        </Button>
      </header>

      {rules.length === 0 ? (
        <EmptyState
          title="규칙이 없습니다"
          description="기본 라우터와 fallback 체인만으로 동작합니다. 트래픽 패턴에 따라 분기가 필요하면 규칙을 추가하세요."
          action={
            <Button size="sm" variant="secondary" onClick={onAdd}>
              규칙 추가
            </Button>
          }
        />
      ) : (
        <ul
          data-testid="routing-rules-list"
          className="flex flex-col divide-y divide-(--border)"
        >
          {rules.map((rule) => (
            <li
              key={rule.id}
              data-testid={`routing-rule-${rule.id}`}
              className="flex items-start justify-between gap-3 py-3 first:pt-0 last:pb-0"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <p className="text-sm font-medium text-(--foreground-strong)">
                  {rule.name}
                </p>
                <p className="font-mono text-xs text-(--muted-foreground)">
                  trigger: {rule.trigger}
                </p>
                <p className="text-xs text-(--muted-foreground)">
                  우선순위 {rule.providerOrder.length}개 · 한도 ${rule.dailyBudgetUsd.toFixed(2)} /일
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onEdit(rule)}
                data-testid={`routing-rule-${rule.id}-edit`}
              >
                편집
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
