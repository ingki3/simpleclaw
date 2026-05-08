"use client";

/**
 * TracesList — admin.pen `kGAWN` "이벤트 / 트레이스" 표 + 4-variant.
 *
 * 검색어가 있으면 메시지 내 일치 substring 을 `<mark>` 로 강조한다.
 * 행 클릭 → 부모(page) 의 `onSelect` 가 trace_id 로 Trace Detail Modal 을 연다.
 *
 * "더 보기" 페이지네이션 — 첫 화면에 PAGE_SIZE 개만 노출하고 누적 노출.
 * 무한 스크롤은 본 단계 (정적 fixture) 에선 과한 비용 — 버튼 한 번에 다음 페이지를 표시한다.
 */

import { useState } from "react";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import { highlightMatches, type LogEvent, type LogLevel } from "../_data";

export type TracesListState = "default" | "empty" | "loading" | "error";

interface TracesListProps {
  state: TracesListState;
  events?: readonly LogEvent[];
  /** 검색어 — 메시지 하이라이트 + 필터된 빈 결과 안내. */
  searchQuery?: string;
  /** 행 클릭 — trace_id 가 있을 때만 호출됨. */
  onSelect?: (event: LogEvent) => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  className?: string;
}

const PAGE_SIZE = 10;
const SKELETON_COUNT = 6;

/** Level 배지의 tone 매핑 — DESIGN.md §5 색-라벨 일관 원칙. */
const LEVEL_TONE: Record<LogLevel, "neutral" | "info" | "warning" | "danger"> = {
  debug: "neutral",
  info: "info",
  warn: "warning",
  error: "danger",
};

const LEVEL_LABEL: Record<LogLevel, string> = {
  debug: "DEBUG",
  info: "INFO",
  warn: "WARN",
  error: "ERROR",
};

export function TracesList({
  state,
  events = [],
  searchQuery,
  onSelect,
  errorMessage = "로그 이벤트를 불러오지 못했습니다.",
  onRetry,
  className,
}: TracesListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="traces-list"
      data-state={state}
      aria-label="이벤트와 트레이스"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <ListEmpty filtered={false} /> : null}
      {state === "default" ? (
        events.length === 0 ? (
          <ListEmpty filtered={isFiltered} />
        ) : (
          <Table
            events={events}
            searchQuery={searchQuery}
            onSelect={onSelect}
          />
        )
      ) : null}
    </section>
  );
}

function Table({
  events,
  searchQuery,
  onSelect,
}: {
  events: readonly LogEvent[];
  searchQuery?: string;
  onSelect?: (event: LogEvent) => void;
}) {
  const [pageCount, setPageCount] = useState(1);
  const limit = pageCount * PAGE_SIZE;
  const visible = events.slice(0, limit);
  const hasMore = events.length > limit;

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)">
        <table
          data-testid="traces-table"
          className="w-full table-fixed border-collapse text-left text-sm"
        >
          <colgroup>
            <col style={{ width: "12%" }} />
            <col style={{ width: "10%" }} />
            <col style={{ width: "18%" }} />
            <col style={{ width: "52%" }} />
            <col style={{ width: "8%" }} />
          </colgroup>
          <thead className="border-b border-(--border) bg-(--surface) text-xs uppercase tracking-wide text-(--muted-foreground)">
            <tr>
              <th className="px-3 py-2 font-medium">시각</th>
              <th className="px-3 py-2 font-medium">레벨</th>
              <th className="px-3 py-2 font-medium">서비스</th>
              <th className="px-3 py-2 font-medium">메시지</th>
              <th className="px-3 py-2 text-right font-medium">trace</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((event) => {
              const clickable = Boolean(event.traceId && onSelect);
              return (
                <tr
                  key={event.id}
                  data-testid={`traces-row-${event.id}`}
                  data-trace-id={event.traceId ?? ""}
                  data-clickable={clickable || undefined}
                  className={cn(
                    "border-b border-(--border) last:border-b-0 align-top",
                    clickable
                      ? "cursor-pointer hover:bg-(--surface)"
                      : "cursor-default",
                  )}
                  onClick={() => {
                    if (clickable && onSelect) onSelect(event);
                  }}
                >
                  <td className="px-3 py-2 font-mono text-xs tabular-nums text-(--muted-foreground)">
                    {formatTime(event.timestamp)}
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone={LEVEL_TONE[event.level]} size="sm">
                      {LEVEL_LABEL[event.level]}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-(--foreground)">
                    {event.service}
                  </td>
                  <td className="px-3 py-2 text-(--foreground)">
                    <Highlighted
                      text={event.message}
                      query={searchQuery ?? ""}
                      data-testid={`traces-row-${event.id}-message`}
                    />
                  </td>
                  <td className="px-3 py-2 text-right">
                    {event.traceId ? (
                      <span
                        data-testid={`traces-row-${event.id}-trace`}
                        className="inline-flex items-center rounded-(--radius-sm) bg-(--primary-tint) px-1.5 py-0.5 font-mono text-[11px] text-(--primary)"
                      >
                        ↗
                      </span>
                    ) : (
                      <span aria-hidden className="text-(--muted-foreground)">
                        —
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between text-xs text-(--muted-foreground)">
        <span data-testid="traces-list-count">
          {visible.length} / {events.length} 건 표시
        </span>
        {hasMore ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setPageCount((c) => c + 1)}
            data-testid="traces-list-more"
          >
            더 보기
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** 메시지 하이라이트 — query 와 일치 substring 을 `<mark>` 로 감싼다. */
function Highlighted({
  text,
  query,
  ...rest
}: {
  text: string;
  query: string;
  "data-testid"?: string;
}) {
  const chunks = highlightMatches(text, query);
  return (
    <span data-testid={rest["data-testid"]}>
      {chunks.map((chunk, i) =>
        chunk.match ? (
          <mark
            key={i}
            data-testid="traces-highlight"
            className="rounded-(--radius-sm) bg-(--color-warning-bg) px-0.5 text-(--color-warning)"
          >
            {chunk.text}
          </mark>
        ) : (
          <span key={i}>{chunk.text}</span>
        ),
      )}
    </span>
  );
}

/** ISO timestamp → hh:mm:ss (UTC). 시각만 노출 — 날짜는 헤더에서 별도 표기. */
function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="로그 이벤트 로딩 중"
      data-testid="traces-list-loading"
      className="overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse items-center gap-4 border-b border-(--border) px-3 py-3 last:border-b-0"
        >
          <div className="h-3 w-16 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-4 w-12 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-32 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 flex-1 rounded-(--radius-sm) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({ filtered }: { filtered: boolean }) {
  if (filtered) {
    return (
      <div data-testid="traces-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="조건에 맞는 이벤트가 없어요"
          description="검색어/레벨/시간 범위를 조정해 보세요."
        />
      </div>
    );
  }
  return (
    <div data-testid="traces-list-empty" data-empty-reason="none">
      <EmptyState
        title="아직 수집된 로그가 없어요"
        description="에이전트가 작업을 수행하면 여기에 이벤트와 trace 가 쌓입니다."
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
      data-testid="traces-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <Badge tone="danger" size="sm">
          logging
        </Badge>
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
          data-testid="traces-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
