"use client";

/**
 * AuditList — admin.pen `Auu2Y` "Audit 표" + 4-variant.
 *
 * 표는 AuditEntry 리유저블(Molecular) 을 그대로 행으로 사용한다 (BIZ-109 P2).
 * 행 우측 액션 슬롯에는 Undo 버튼이 노출되며, `outcome === "applied"` 일 때만 활성.
 *
 * "더 보기" 페이지네이션 — 첫 화면에 PAGE_SIZE 개만 노출하고 누적 노출.
 * 무한 스크롤은 본 단계 (정적 fixture) 에선 과한 비용이라 버튼 한 번에 다음 페이지를 표시.
 */

import { useState } from "react";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { AuditEntry as AuditEntryRow } from "@/design/molecules/AuditEntry";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import { canUndo, type AuditEntry } from "../_data";

export type AuditListState = "default" | "empty" | "loading" | "error";

interface AuditListProps {
  state: AuditListState;
  entries?: readonly AuditEntry[];
  /** 검색 입력값 — 필터된 빈 결과 안내 분기에 사용. */
  searchQuery?: string;
  /** "Undo" 클릭 — applied 행에서만 호출됨. */
  onUndo?: (entry: AuditEntry) => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  className?: string;
}

const PAGE_SIZE = 10;
const SKELETON_COUNT = 6;

export function AuditList({
  state,
  entries = [],
  searchQuery,
  onUndo,
  errorMessage = "감사 로그를 불러오지 못했습니다.",
  onRetry,
  className,
}: AuditListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="audit-list"
      data-state={state}
      aria-label="감사 로그"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <ListEmpty filtered={false} /> : null}
      {state === "default" ? (
        entries.length === 0 ? (
          <ListEmpty filtered={isFiltered} />
        ) : (
          <Rows entries={entries} onUndo={onUndo} />
        )
      ) : null}
    </section>
  );
}

function Rows({
  entries,
  onUndo,
}: {
  entries: readonly AuditEntry[];
  onUndo?: (entry: AuditEntry) => void;
}) {
  const [pageCount, setPageCount] = useState(1);
  const limit = pageCount * PAGE_SIZE;
  const visible = entries.slice(0, limit);
  const hasMore = entries.length > limit;

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)">
        <ul
          data-testid="audit-rows"
          className="flex flex-col divide-y divide-(--border) px-4"
        >
          {visible.map((entry) => (
            <li key={entry.id} data-testid={`audit-row-${entry.id}`}>
              <AuditEntryRow
                actor={entry.actor}
                action={entry.action}
                target={<TargetCell entry={entry} />}
                outcome={entry.outcome}
                traceId={entry.traceId}
                timestamp={formatDateTime(entry.timestamp)}
                action_slot={
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!canUndo(entry) || !onUndo}
                    data-testid={`audit-row-${entry.id}-undo`}
                    onClick={() => {
                      if (canUndo(entry) && onUndo) onUndo(entry);
                    }}
                  >
                    되돌리기
                  </Button>
                }
              />
            </li>
          ))}
        </ul>
      </div>
      <div className="flex items-center justify-between text-xs text-(--muted-foreground)">
        <span data-testid="audit-list-count">
          {visible.length} / {entries.length} 건 표시
        </span>
        {hasMore ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setPageCount((c) => c + 1)}
            data-testid="audit-list-more"
          >
            더 보기
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** 표 가운데 셀 — target + (선택) before → after. AuditEntry molecule 의 target 슬롯에 들어간다. */
function TargetCell({ entry }: { entry: AuditEntry }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5 font-mono text-xs">
      <span className="text-(--foreground)">{entry.target}</span>
      {entry.before !== undefined || entry.after !== undefined ? (
        <span className="flex items-center gap-1 text-(--muted-foreground)">
          <span className="text-(--muted-foreground)">·</span>
          {entry.before !== undefined ? (
            <span data-testid={`audit-row-${entry.id}-before`}>
              {entry.before}
            </span>
          ) : null}
          {entry.before !== undefined && entry.after !== undefined ? (
            <span aria-hidden>→</span>
          ) : null}
          {entry.after !== undefined ? (
            <span
              data-testid={`audit-row-${entry.id}-after`}
              className="text-(--foreground)"
            >
              {entry.after}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

/** ISO timestamp → yyyy-MM-dd HH:mm (UTC). 표 우상단 보조 라벨. */
function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const yyyy = d.getUTCFullYear();
  const MM = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${yyyy}-${MM}-${dd} ${hh}:${mm}`;
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="감사 로그 로딩 중"
      data-testid="audit-list-loading"
      className="overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse items-center gap-4 border-b border-(--border) px-3 py-3 last:border-b-0"
        >
          <div className="h-5 w-12 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-20 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-32 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 flex-1 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-7 w-16 rounded-(--radius-sm) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({ filtered }: { filtered: boolean }) {
  if (filtered) {
    return (
      <div data-testid="audit-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="조건에 맞는 감사 로그가 없어요"
          description="검색어/영역/액션/시간 범위를 조정해 보세요."
        />
      </div>
    );
  }
  return (
    <div data-testid="audit-list-empty" data-empty-reason="none">
      <EmptyState
        title="아직 기록된 감사 로그가 없어요"
        description="운영자/에이전트의 변경이 발생하면 여기에 액션 단위로 쌓입니다."
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
      data-testid="audit-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <Badge tone="danger" size="sm">
          audit
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
          data-testid="audit-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
