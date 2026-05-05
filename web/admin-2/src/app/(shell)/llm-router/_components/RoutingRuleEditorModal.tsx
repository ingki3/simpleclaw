"use client";

/**
 * RoutingRuleEditorModal — admin.pen `Sms7l` (Routing Rule Editor) 박제.
 *
 * 폼:
 *  - 규칙 이름 (1줄)
 *  - 트리거 (prompt 키워드 / 정규식)
 *  - 프로바이더 우선순위 — 드래그 미구현, ↑/↓ 버튼으로 재정렬
 *  - 비용 한도 (USD/일)
 *  - DryRunCard — 변경 전/후 비교 + "적용 (dry-run)" 버튼
 *
 * 적용 버튼은 항상 dry-run 으로 동작 — 실제 라우팅 변경은 별도 confirm gate.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { DryRunCard } from "@/design/molecules/DryRunCard";
import type { RoutingRule, RoutingRuleProvider } from "../_data";
import { Modal } from "./Modal";

export interface RoutingRuleFormValue {
  id: string;
  name: string;
  trigger: string;
  providerOrder: RoutingRuleProvider[];
  dailyBudgetUsd: number;
}

interface RoutingRuleEditorModalProps {
  open: boolean;
  /** 편집 대상 — null 이면 modal 은 닫힌 상태로 본다. */
  rule: RoutingRule | null;
  onClose: () => void;
  /** dry-run 결과를 부모가 노출. 본 단계에선 콘솔 로그 또는 toast 가 적합. */
  onDryRun: (value: RoutingRuleFormValue) => void;
}

export function RoutingRuleEditorModal({
  open,
  rule,
  onClose,
  onDryRun,
}: RoutingRuleEditorModalProps) {
  const [form, setForm] = useState<RoutingRuleFormValue | null>(null);

  useEffect(() => {
    if (rule) {
      // providerOrder 는 mutate 하지 않도록 항상 새 배열로 prefill.
      setForm({
        id: rule.id,
        name: rule.name,
        trigger: rule.trigger,
        providerOrder: [...rule.providerOrder],
        dailyBudgetUsd: rule.dailyBudgetUsd,
      });
    }
  }, [rule, open]);

  if (!open || !rule || !form) {
    return (
      <Modal
        open={false}
        onClose={onClose}
        title={null}
        footer={null}
      >
        {null}
      </Modal>
    );
  }

  const move = (idx: number, dir: -1 | 1) => {
    const next = [...form.providerOrder];
    const target = idx + dir;
    if (target < 0 || target >= next.length) return;
    [next[idx], next[target]] = [next[target]!, next[idx]!];
    setForm({ ...form, providerOrder: next });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="lg"
      data-testid="routing-rule-modal"
      title={
        <div className="flex items-center gap-2">
          <span aria-hidden>⌥</span>
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            라우팅 규칙 편집
          </h2>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="routing-rule-cancel"
          >
            취소
          </Button>
          <Button
            variant="primary"
            onClick={() => onDryRun(form)}
            data-testid="routing-rule-dryrun"
          >
            적용 (dry-run)
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="rule-name">규칙 이름</Label>
        <Input
          id="rule-name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.currentTarget.value })}
          data-testid="rule-name"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="rule-trigger">트리거 (prompt 키워드 / 정규식)</Label>
        <Input
          id="rule-trigger"
          value={form.trigger}
          onChange={(e) =>
            setForm({ ...form, trigger: e.currentTarget.value })
          }
          data-testid="rule-trigger"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between">
          <Label>프로바이더 우선순위</Label>
          <span className="text-xs text-(--muted-foreground)">
            ↑/↓ 로 순서 변경
          </span>
        </div>
        <ol
          data-testid="rule-provider-order"
          className="flex flex-col gap-1.5"
        >
          {form.providerOrder.map((p, idx) => (
            <li
              key={`${p.providerId}-${idx}`}
              data-testid={`rule-provider-${idx}`}
              className="flex items-center gap-2 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2"
            >
              <span aria-hidden className="text-(--muted-foreground)">
                ⋮⋮
              </span>
              <span className="font-mono text-xs text-(--muted-foreground)">
                {idx + 1}
              </span>
              <span className="flex-1 truncate text-sm text-(--foreground)">
                {p.label}
              </span>
              <span className="font-mono text-xs text-(--muted-foreground)">
                {p.rate} · {p.latency}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => move(idx, -1)}
                disabled={idx === 0}
                aria-label={`우선순위 올리기: ${p.label}`}
                data-testid={`rule-provider-${idx}-up`}
              >
                ↑
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => move(idx, 1)}
                disabled={idx === form.providerOrder.length - 1}
                aria-label={`우선순위 내리기: ${p.label}`}
                data-testid={`rule-provider-${idx}-down`}
              >
                ↓
              </Button>
            </li>
          ))}
        </ol>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="rule-budget">비용 한도 (USD/일)</Label>
        <Input
          id="rule-budget"
          type="number"
          min={0}
          step="0.01"
          value={form.dailyBudgetUsd}
          onChange={(e) =>
            setForm({
              ...form,
              dailyBudgetUsd: Number(e.currentTarget.value),
            })
          }
          data-testid="rule-budget"
        />
        <p className="text-xs text-(--muted-foreground)">
          (초과 시 fallback 강제)
        </p>
      </div>

      <DryRunCard
        data-testid="rule-dryrun-preview"
        before={
          <pre className="whitespace-pre-wrap font-mono text-xs">
            {formatPreview(rule)}
          </pre>
        }
        after={
          <pre className="whitespace-pre-wrap font-mono text-xs">
            {formatPreview({
              ...rule,
              ...form,
              providerOrder: form.providerOrder,
            })}
          </pre>
        }
        impact="규칙 적용 시 평균 단가 약 12% 감소가 예상됩니다 (mock)."
      />
    </Modal>
  );
}

function formatPreview(rule: RoutingRule | RoutingRuleFormValue) {
  const order = rule.providerOrder
    .map((p, i) => `  ${i + 1}. ${p.label}`)
    .join("\n");
  return `name: ${rule.name}
trigger: ${rule.trigger}
providers:
${order}
budget: $${rule.dailyBudgetUsd.toFixed(2)}/day`;
}
