/**
 * ProvidersGrid — 카드 목록 + 4-variant (default/empty/loading/error).
 *
 * DESIGN.md §1 Principle 3 — 모든 영역에 4-variant 시각 박제.
 * S3 Dashboard 의 ActiveProjectsPanel 패턴을 그대로 따른다.
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { RouterProvider } from "../_data";
import { ProviderCard } from "./ProviderCard";

export type ProvidersState = "default" | "empty" | "loading" | "error";

interface ProvidersGridProps {
  state: ProvidersState;
  providers?: readonly RouterProvider[];
  onEdit: (provider: RouterProvider) => void;
  /** Add Provider 버튼 클릭. empty 일 때 EmptyState CTA 도 동일 액션. */
  onAdd: () => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  className?: string;
}

const SKELETON_COUNT = 3;

export function ProvidersGrid({
  state,
  providers = [],
  onEdit,
  onAdd,
  errorMessage = "프로바이더 목록을 불러오지 못했습니다.",
  onRetry,
  className,
}: ProvidersGridProps) {
  return (
    <section
      data-testid="providers-grid"
      data-state={state}
      aria-label="LLM 프로바이더 카드"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <GridLoading /> : null}
      {state === "error" ? (
        <GridError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <GridEmpty onAdd={onAdd} /> : null}
      {state === "default" ? (
        providers.length === 0 ? (
          <GridEmpty onAdd={onAdd} />
        ) : (
          <div
            data-testid="providers-grid-list"
            className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          >
            {providers.map((p) => (
              <ProviderCard key={p.id} provider={p} onEdit={onEdit} />
            ))}
            <button
              type="button"
              onClick={onAdd}
              data-testid="providers-grid-add"
              className="flex min-h-[200px] flex-col items-center justify-center gap-2 rounded-(--radius-l) border border-dashed border-(--border-strong) bg-(--surface) p-5 text-sm text-(--muted-foreground) transition-colors hover:border-(--primary) hover:text-(--primary)"
            >
              <span aria-hidden className="text-2xl">＋</span>
              프로바이더 추가
            </button>
          </div>
        )
      ) : null}
    </section>
  );
}

function GridLoading() {
  return (
    <div
      role="status"
      aria-label="프로바이더 로딩 중"
      data-testid="providers-grid-loading"
      className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex h-[260px] animate-pulse flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5"
        >
          <div className="h-5 w-24 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-3/4 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-2/3 rounded-(--radius-sm) bg-(--surface)" />
          <div className="mt-auto h-9 w-full rounded-(--radius-m) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function GridEmpty({ onAdd }: { onAdd: () => void }) {
  return (
    <div data-testid="providers-grid-empty">
      <EmptyState
        title="등록된 프로바이더가 없습니다"
        description="LLM 라우터를 사용하려면 최소 1개의 프로바이더가 필요합니다. Anthropic·OpenAI·Gemini 또는 사용자 정의 엔드포인트를 추가하세요."
        action={
          <Button size="sm" variant="primary" onClick={onAdd}>
            프로바이더 추가
          </Button>
        }
      />
    </div>
  );
}

function GridError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="providers-grid-error"
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
          data-testid="providers-grid-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
