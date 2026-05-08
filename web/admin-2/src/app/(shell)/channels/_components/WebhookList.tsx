"use client";

/**
 * WebhookList — admin.pen `weuuW` 하단 "Webhooks · 정책" 카드 + 4-variant.
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — h2 "Webhooks · 정책" + 우측 메타 (endpoint 수 · 24h 요청 · 4xx 비율)
 *  2) 정책 입력 행 — Rate limit / Max body / Concurrency / Signature
 *  3) endpoint 표 — URL · 용도 · 24h 요청 · enabled Switch · 편집 IconButton
 *
 * variant: default / loading / empty / error — 영역 일관 SSOT.
 * 카드 클릭 자체는 동작 없음 — Switch / 편집 IconButton 만 트리거.
 */

import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { Label } from "@/design/atoms/Label";
import { Select } from "@/design/atoms/Select";
import { StatusPill } from "@/design/atoms/StatusPill";
import { Switch } from "@/design/atoms/Switch";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type {
  WebhookEndpoint,
  WebhookPolicy,
  WebhooksConfig,
} from "../_data";

export type WebhookListState = "default" | "loading" | "empty" | "error";

interface WebhookListProps {
  state: WebhookListState;
  webhooks?: WebhooksConfig;
  /** 정책 입력 값 — 부모 controlled. */
  policy: WebhookPolicy;
  onPolicyChange: (next: WebhookPolicy) => void;
  /** Endpoint switch 토글. */
  onToggleEndpoint: (id: string, next: boolean) => void;
  /** Endpoint 행의 "편집" IconButton — Webhook Edit modal 트리거. */
  onEditEndpoint: (endpoint: WebhookEndpoint) => void;
  /** error variant 전용 — 재시도 트리거. */
  onRetry?: () => void;
  errorMessage?: string;
  className?: string;
}

const SIGNATURE_OPTIONS = [
  { value: "HMAC-SHA256", label: "HMAC-SHA256" },
  { value: "HMAC-SHA512", label: "HMAC-SHA512" },
  { value: "Ed25519", label: "Ed25519" },
];

export function WebhookList({
  state,
  webhooks,
  policy,
  onPolicyChange,
  onToggleEndpoint,
  onEditEndpoint,
  onRetry,
  errorMessage = "웹훅 정책을 불러오지 못했습니다.",
  className,
}: WebhookListProps) {
  return (
    <section
      data-testid="webhook-list"
      data-state={state}
      aria-label="웹훅 정책"
      aria-busy={state === "loading" || undefined}
      className={cn(
        "flex flex-col gap-4 rounded-(--radius-l) border border-(--border) bg-(--card) p-6",
        className,
      )}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold text-(--foreground-strong)">
          Webhooks · 정책
        </h2>
        {state === "default" && webhooks ? (
          <span
            data-testid="webhook-list-meta"
            className="text-xs text-(--muted-foreground)"
          >
            {webhooks.endpoints.length} endpoint · 24h{" "}
            <span className="tabular-nums">
              {webhooks.reqLast24hTotal.toLocaleString()}
            </span>{" "}
            req ·{" "}
            <span className="tabular-nums">
              {(webhooks.errorRate24h * 100).toFixed(1)}%
            </span>{" "}
            4xx
          </span>
        ) : null}
      </header>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <PolicyField id="webhook-policy-rate-limit" label="Rate limit (req/s)">
          <Input
            id="webhook-policy-rate-limit"
            type="number"
            min={0}
            value={policy.rateLimitPerSec}
            onChange={(e) =>
              onPolicyChange({
                ...policy,
                rateLimitPerSec: Number(e.currentTarget.value),
              })
            }
            data-testid="webhook-policy-rate-limit"
          />
        </PolicyField>
        <PolicyField id="webhook-policy-max-body" label="Max body (KB)">
          <Input
            id="webhook-policy-max-body"
            type="number"
            min={0}
            value={policy.maxBodyKb}
            onChange={(e) =>
              onPolicyChange({
                ...policy,
                maxBodyKb: Number(e.currentTarget.value),
              })
            }
            data-testid="webhook-policy-max-body"
          />
        </PolicyField>
        <PolicyField id="webhook-policy-concurrency" label="Concurrency">
          <Input
            id="webhook-policy-concurrency"
            type="number"
            min={1}
            value={policy.concurrency}
            onChange={(e) =>
              onPolicyChange({
                ...policy,
                concurrency: Number(e.currentTarget.value),
              })
            }
            data-testid="webhook-policy-concurrency"
          />
        </PolicyField>
        <PolicyField id="webhook-policy-signature" label="Signature">
          <Select
            id="webhook-policy-signature"
            options={SIGNATURE_OPTIONS}
            value={policy.signature}
            onChange={(e) =>
              onPolicyChange({
                ...policy,
                signature: e.currentTarget.value,
              })
            }
            data-testid="webhook-policy-signature"
          />
        </PolicyField>
      </div>

      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <ListEmpty /> : null}
      {state === "default" && webhooks ? (
        webhooks.endpoints.length === 0 ? (
          <ListEmpty />
        ) : (
          <EndpointTable
            endpoints={webhooks.endpoints}
            onToggle={onToggleEndpoint}
            onEdit={onEditEndpoint}
          />
        )
      ) : null}
    </section>
  );
}

function PolicyField({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

function EndpointTable({
  endpoints,
  onToggle,
  onEdit,
}: {
  endpoints: readonly WebhookEndpoint[];
  onToggle: (id: string, next: boolean) => void;
  onEdit: (endpoint: WebhookEndpoint) => void;
}) {
  return (
    <div
      data-testid="webhook-list-table"
      className="overflow-hidden rounded-(--radius-m) border border-(--border)"
    >
      <div className="grid grid-cols-[1fr_220px_120px_80px_80px] items-center gap-3 bg-(--surface) px-4 py-2 text-xs font-medium uppercase tracking-wide text-(--muted-foreground)">
        <span>Endpoint</span>
        <span>용도</span>
        <span className="text-right">24h</span>
        <span className="text-center">활성</span>
        <span className="text-right">편집</span>
      </div>
      <ul className="divide-y divide-(--border) bg-(--card)">
        {endpoints.map((endpoint) => (
          <li
            key={endpoint.id}
            data-testid={`webhook-endpoint-${endpoint.id}`}
            data-enabled={endpoint.enabled || undefined}
            className="grid grid-cols-[1fr_220px_120px_80px_80px] items-center gap-3 px-4 py-3 text-sm"
          >
            <span className="truncate font-mono text-xs text-(--foreground)">
              {endpoint.url}
            </span>
            <span className="truncate text-(--muted-foreground)">
              {endpoint.purpose}
            </span>
            <span className="text-right tabular-nums text-(--muted-foreground)">
              {endpoint.reqLast24h.toLocaleString()} req
            </span>
            <span className="flex justify-center">
              <Switch
                checked={endpoint.enabled}
                onCheckedChange={(next) => onToggle(endpoint.id, next)}
                label={`${endpoint.id} 활성화`}
                data-testid={`webhook-endpoint-${endpoint.id}-toggle`}
              />
            </span>
            <span className="flex justify-end">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onEdit(endpoint)}
                data-testid={`webhook-endpoint-${endpoint.id}-edit`}
              >
                편집
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="웹훅 목록 로딩 중"
      data-testid="webhook-list-loading"
      className="flex flex-col gap-2"
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="h-12 animate-pulse rounded-(--radius-m) bg-(--surface)"
        />
      ))}
    </div>
  );
}

function ListEmpty() {
  return (
    <div data-testid="webhook-list-empty">
      <EmptyState
        title="등록된 웹훅이 없어요"
        description="endpoints/webhooks.yaml 에 새 endpoint 를 추가하면 이곳에 표시됩니다."
      />
    </div>
  );
}

function ListError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="webhook-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <span className="font-medium text-(--color-error)">{message}</span>
      </div>
      <p className="text-xs text-(--muted-foreground)">
        잠시 후 자동 재시도 — 즉시 다시 시도하려면 아래 버튼을 누르세요.
      </p>
      {onRetry ? (
        <Button
          size="sm"
          variant="secondary"
          onClick={onRetry}
          data-testid="webhook-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
