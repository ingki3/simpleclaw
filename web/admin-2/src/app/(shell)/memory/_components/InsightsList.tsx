"use client";

/**
 * InsightsList — admin.pen `oeMlR` (Screen 06.5 · Insights) 박제.
 *
 * Memory 화면의 메인 영역. dreaming 이 추출한 인사이트 큐를 검토 카드로
 * 노출하고, Accept / Edit / Reject / Source / Defer 액션을 제공한다.
 *
 * DESIGN.md §1 Principle 3 — default / loading / empty / error 4-variant 박제.
 * variant 검증은 page.tsx 의 `?insights=loading|empty|error` 쿼리로 강제.
 *
 * 카드 내 액션 콜백은 부모(page) 가 모달/Drawer 토글을 담당하도록 설계 —
 * 본 컴포넌트는 시각만 책임지고 mutation/네트워크 호출에 직접 관여하지 않는다.
 */

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { MemoryInsight } from "../_data";
import { formatRelative } from "./ActiveProjectsPanel";

export type InsightsListState = "default" | "empty" | "loading" | "error";

interface InsightsListProps {
  state: InsightsListState;
  insights?: readonly MemoryInsight[];
  /** 검토 액션 — 부모가 fixture/state 갱신 + 토스트 담당. */
  onAccept?: (id: string) => void;
  onReject?: (insight: MemoryInsight) => void;
  /** Source Drawer 진입. */
  onOpenSource?: (insight: MemoryInsight) => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  /** 검색어 — 필터된 빈 결과 안내에 사용. */
  searchQuery?: string;
  className?: string;
}

const SKELETON_COUNT = 4;

export function InsightsList({
  state,
  insights = [],
  onAccept,
  onReject,
  onOpenSource,
  errorMessage = "인사이트 큐를 불러오지 못했습니다.",
  onRetry,
  searchQuery,
  className,
}: InsightsListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="memory-insights-list"
      data-state={state}
      aria-label="Insights 큐"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <ListEmpty filtered={false} /> : null}
      {state === "default" ? (
        insights.length === 0 ? (
          <ListEmpty filtered={isFiltered} />
        ) : (
          <ul
            data-testid="memory-insights-cards"
            className="flex flex-col gap-2"
          >
            {insights.map((insight) => (
              <InsightRow
                key={insight.id}
                insight={insight}
                onAccept={onAccept}
                onReject={onReject}
                onOpenSource={onOpenSource}
              />
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}

function InsightRow({
  insight,
  onAccept,
  onReject,
  onOpenSource,
}: {
  insight: MemoryInsight;
  onAccept?: (id: string) => void;
  onReject?: (insight: MemoryInsight) => void;
  onOpenSource?: (insight: MemoryInsight) => void;
}) {
  const conf = (insight.confidence * 100).toFixed(0);
  const confTone =
    insight.confidence >= 0.7
      ? "success"
      : insight.confidence >= 0.4
        ? "warning"
        : "error";

  // cron 자동 실행에서 추출된 잡음은 reject-only — 자동 채택 차단.
  const rejectOnly = insight.cronNoise === true;

  return (
    <li
      data-testid={`memory-insight-${insight.id}`}
      data-cron-noise={rejectOnly || undefined}
      className="flex flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-3"
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-(--muted-foreground)">
          {insight.topic}
        </span>
        <Badge tone="brand" size="sm">
          {insight.lifecycle}
        </Badge>
        <Badge tone="neutral" size="sm">
          {insight.channel}
        </Badge>
        {rejectOnly ? (
          <Badge tone="warning" size="sm">
            cron-noise
          </Badge>
        ) : null}
        <StatusPill tone={confTone}>conf {conf}%</StatusPill>
        <span className="ml-auto text-[11px] text-(--muted-foreground)">
          근거 {insight.evidenceCount}건 · {formatRelative(insight.updatedAt)}
        </span>
      </header>
      <p className="break-words text-sm text-(--foreground)">
        {insight.text}
      </p>
      <footer
        data-testid={`memory-insight-${insight.id}-actions`}
        className="flex flex-wrap items-center justify-end gap-2"
      >
        {onOpenSource ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onOpenSource(insight)}
            data-testid={`memory-insight-${insight.id}-source`}
          >
            출처 보기
          </Button>
        ) : null}
        {onReject ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onReject(insight)}
            data-testid={`memory-insight-${insight.id}-reject`}
          >
            거절
          </Button>
        ) : null}
        {onAccept && !rejectOnly ? (
          <Button
            size="sm"
            variant="primary"
            onClick={() => onAccept(insight.id)}
            data-testid={`memory-insight-${insight.id}-accept`}
          >
            채택
          </Button>
        ) : null}
      </footer>
    </li>
  );
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="인사이트 큐 로딩 중"
      data-testid="memory-insights-list-loading"
      className="flex flex-col gap-2"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-3"
        >
          <div className="flex items-center gap-2">
            <span className="h-3 w-24 rounded-(--radius-sm) bg-(--surface)" />
            <span className="h-3 w-16 rounded-(--radius-pill) bg-(--surface)" />
            <span className="ml-auto h-3 w-20 rounded-(--radius-sm) bg-(--surface)" />
          </div>
          <span className="h-4 w-3/4 rounded-(--radius-sm) bg-(--surface)" />
          <span className="h-4 w-1/2 rounded-(--radius-sm) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({ filtered }: { filtered: boolean }) {
  if (filtered) {
    return (
      <div data-testid="memory-insights-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="검색 결과가 없어요"
          description="다른 키워드로 다시 시도하거나, 검색을 비워 전체 큐를 살펴보세요."
        />
      </div>
    );
  }
  return (
    <div data-testid="memory-insights-list-empty" data-empty-reason="none">
      <EmptyState
        title="검토할 인사이트가 없어요"
        description="dreaming 사이클을 한 번 돌리면 여기에 후보 인사이트가 쌓입니다."
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
      data-testid="memory-insights-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <Badge tone="danger" size="sm">
          dreaming
        </Badge>
        <span className="font-medium text-(--color-error)">{message}</span>
      </div>
      <p className="text-xs text-(--muted-foreground)">
        dreaming 사이드카가 일시적으로 잠겼거나 데몬이 재시작 중일 수 있어요.
        잠시 후 자동 재시도되지만, 즉시 다시 시도하려면 아래 버튼을 누르세요.
      </p>
      {onRetry ? (
        <Button
          size="sm"
          variant="secondary"
          onClick={onRetry}
          data-testid="memory-insights-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
