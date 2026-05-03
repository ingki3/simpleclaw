"use client";

/**
 * Audit 화면 — admin.pen Screen 10 / docs/admin-requirements.md §4.
 *
 * 책임은 *감사 로그를 운영자에게 노출하고 5분 윈도 안에서 되돌리기를 안전하게 적용*하는 것.
 *
 * 구성:
 *  1. 페이지 헤더 — 타이틀 + 기록 개수 요약.
 *  2. AuditFilters — 날짜·영역·액션·결과·페이지 크기 + CSV 내보내기.
 *  3. 시간 역순 타임라인 — 백엔드의 ``GET /admin/v1/audit``는 시간 오름차순으로
 *     반환하므로, 화면에서 한 번 뒤집어 최신이 위에 오도록 한다.
 *  4. Drawer — 항목 클릭 시 우측에서 슬라이드, AuditDetail로 diff/Undo 표시.
 *  5. ConfirmGate — Undo는 *대문자 ``UNDO`` 일치*를 요구해 잘못 누른 클릭을 막는다.
 *
 * 데이터 흐름:
 *  - 필터 상태가 변하면 ``useAdminResource`` path에 query를 다시 만들어 자동 재조회.
 *  - Undo 성공 시 (a) 토스트, (b) 목록 강제 갱신, (c) Drawer 닫기.
 *  - Undo는 새 audit entry를 만든다(백엔드 정책) — 갱신된 목록의 맨 위에 노출된다.
 */

import { useEffect, useMemo, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { useToast } from "@/lib/toast";
import { useAdminResource } from "@/lib/api/use-admin-resource";
import { fetchAdmin, AdminApiError } from "@/lib/api/fetch-admin";
import { Drawer, ConfirmGate } from "@/components/primitives";
import { Badge } from "@/components/atoms/Badge";
import { StatusPill } from "@/components/atoms/StatusPill";
import { Button } from "@/components/atoms/Button";
import {
  AuditFilters,
  buildAuditQuery,
  DEFAULT_FILTERS,
  type AuditFilterState,
} from "./_components/AuditFilters";
import { AuditDetail } from "./_components/AuditDetail";
import { defaultFilename, downloadAuditCsv } from "./_components/csv";
import {
  formatPayloadInline,
  formatRelativeTs,
  isUndoableNow,
  outcomeTone,
  type AuditEntryDTO,
} from "./_components/audit-utils";

interface AuditListResponse {
  entries: AuditEntryDTO[];
}

export default function AuditPage() {
  const toast = useToast();
  const [filters, setFilters] = useState<AuditFilterState>(DEFAULT_FILTERS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pendingUndo, setPendingUndo] = useState<AuditEntryDTO | null>(null);
  const [isUndoing, setIsUndoing] = useState(false);

  // path는 필터 변경마다 새로 생성 — useAdminResource는 path 키 변화 시 자동 재조회.
  const path = useMemo(() => {
    const qs = buildAuditQuery(filters).toString();
    return `/admin/v1/audit${qs ? `?${qs}` : ""}`;
  }, [filters]);

  const list = useAdminResource<AuditListResponse>(path);

  // 백엔드는 오름차순(오래된 게 먼저). 화면은 최신이 위.
  const entries = useMemo(() => {
    if (!list.data?.entries) return [];
    return [...list.data.entries].reverse();
  }, [list.data]);

  const selected = useMemo(
    () => entries.find((e) => e.id === selectedId) ?? null,
    [entries, selectedId],
  );

  // Drawer가 열려 있는데 갱신으로 항목이 사라진 경우 — 자동 닫기.
  useEffect(() => {
    if (selectedId && !selected && !list.isLoading) {
      setSelectedId(null);
    }
  }, [selectedId, selected, list.isLoading]);

  function handleResetFilters() {
    setFilters(DEFAULT_FILTERS);
  }

  function handleExport() {
    if (entries.length === 0) return;
    try {
      downloadAuditCsv(entries, defaultFilename());
      toast.push({
        tone: "info",
        title: `${entries.length}건의 감사 항목을 CSV로 내보냈어요.`,
      });
    } catch (err) {
      toast.push({
        tone: "error",
        title: "CSV 내보내기에 실패했어요.",
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function performUndo(entry: AuditEntryDTO) {
    setIsUndoing(true);
    try {
      await fetchAdmin(`/admin/v1/audit/${encodeURIComponent(entry.id)}/undo`, {
        method: "POST",
      });
      toast.push({
        tone: "success",
        title: "변경을 되돌렸어요.",
        description: `${entry.action} · ${entry.target || entry.area}`,
      });
      setPendingUndo(null);
      setSelectedId(null);
      list.refetch();
    } catch (err) {
      const msg = err instanceof AdminApiError ? err.message : String(err);
      toast.push({
        tone: "error",
        title: "되돌리기에 실패했어요.",
        description: msg,
      });
    } finally {
      setIsUndoing(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-start gap-3">
        <ShieldCheck
          size={28}
          strokeWidth={1.5}
          aria-hidden
          className="mt-1 text-[--primary]"
        />
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold text-[--foreground-strong]">
            감사
          </h1>
          <p className="text-sm text-[--muted-foreground]">
            데몬에 적용된 모든 변경을 시간 역순으로 보고, 최근 5분 안의 변경은
            안전하게 되돌립니다. 시크릿 값은 항상 마스킹된 상태로 표시됩니다.
          </p>
        </div>
      </header>

      <AuditFilters
        value={filters}
        onChange={setFilters}
        onReset={handleResetFilters}
        onRefresh={list.refetch}
        onExportCsv={handleExport}
        canExport={entries.length > 0}
        isLoading={list.isLoading || list.isRefreshing}
      />

      {list.error ? (
        <ErrorPanel message={list.error.message} onRetry={list.refetch} />
      ) : list.isLoading && entries.length === 0 ? (
        <SkeletonList />
      ) : entries.length === 0 ? (
        <EmptyState />
      ) : (
        <AuditTimeline
          entries={entries}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      )}

      <Drawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
        title={selected ? selected.action : "감사 상세"}
        description={selected ? selected.target || selected.area : undefined}
        size="lg"
      >
        {selected ? (
          <AuditDetail
            entry={selected}
            isUndoing={isUndoing && pendingUndo?.id === selected.id}
            onUndoRequest={() => setPendingUndo(selected)}
            onViewTrace={(traceId) => {
              window.location.href = `/logs?trace_id=${encodeURIComponent(traceId)}`;
            }}
          />
        ) : null}
      </Drawer>

      <ConfirmGate
        open={!!pendingUndo}
        onOpenChange={(open) => {
          if (!open && !isUndoing) setPendingUndo(null);
        }}
        title="이 변경을 되돌릴까요?"
        description={
          pendingUndo
            ? `${pendingUndo.action} · ${pendingUndo.target || pendingUndo.area} — 이전 값으로 복원되며, 새 감사 항목이 만들어집니다.`
            : ""
        }
        confirmation="UNDO"
        confirmLabel="되돌리기"
        tone="warning"
        isPending={isUndoing}
        onConfirm={async () => {
          if (pendingUndo) await performUndo(pendingUndo);
        }}
      />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// 보조 컴포넌트 — 메인 컴포넌트와 분리해 가독성 유지.
// ──────────────────────────────────────────────────────────────────

interface AuditTimelineProps {
  entries: AuditEntryDTO[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function AuditTimeline({ entries, selectedId, onSelect }: AuditTimelineProps) {
  // 동일 화면에서 최신 30분 내 이벤트는 "최근" 섹션으로 묶고 그 외는 그대로 — 화면이
  // 폭주 시에도 최근 변화에 시선이 먼저 가도록. 단순 1-pass 분할.
  const RECENT_WINDOW_MS = 30 * 60 * 1000;
  const now = Date.now();
  const recent: AuditEntryDTO[] = [];
  const older: AuditEntryDTO[] = [];
  for (const e of entries) {
    const ts = Date.parse(e.ts);
    if (Number.isFinite(ts) && now - ts <= RECENT_WINDOW_MS) {
      recent.push(e);
    } else {
      older.push(e);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {recent.length > 0 ? (
        <TimelineSection
          title="최근 30분"
          entries={recent}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ) : null}
      {older.length > 0 ? (
        <TimelineSection
          title={recent.length > 0 ? "이전" : `${entries.length}건`}
          entries={older}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ) : null}
    </div>
  );
}

interface TimelineSectionProps extends AuditTimelineProps {
  title: string;
}

function TimelineSection({
  title,
  entries,
  selectedId,
  onSelect,
}: TimelineSectionProps) {
  return (
    <section
      aria-label={title}
      className="overflow-hidden rounded-[--radius-l] border border-[--border] bg-[--card]"
    >
      <header className="flex items-center justify-between border-b border-[--border] px-4 py-2">
        <h2 className="text-sm font-semibold text-[--foreground-strong]">
          {title}
        </h2>
        <span className="text-xs text-[--muted-foreground]">
          {entries.length}건
        </span>
      </header>
      <ul role="list" className="divide-y divide-[--border]">
        {entries.map((e) => (
          <AuditRowButton
            key={e.id}
            entry={e}
            selected={selectedId === e.id}
            onSelect={() => onSelect(e.id)}
          />
        ))}
      </ul>
    </section>
  );
}

interface AuditRowButtonProps {
  entry: AuditEntryDTO;
  selected: boolean;
  onSelect: () => void;
}

/**
 * 한 줄짜리 행 — Drawer를 여는 button. AuditRow(공용 컴포넌트)는 undo·trace를
 * 행 내부에서 바로 처리하지만, 본 화면은 *모든 액션을 Drawer로 위임*하므로
 * 행 자체를 클릭 가능한 button으로 두는 편이 흐름이 단순하다.
 */
function AuditRowButton({ entry, selected, onSelect }: AuditRowButtonProps) {
  const tone = outcomeTone(entry.outcome);
  const undoable = isUndoableNow(entry);
  const before = formatPayloadInline(entry.before);
  const after = formatPayloadInline(entry.after);

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={
          "flex w-full flex-col gap-1 px-4 py-3 text-left text-sm transition-colors hover:bg-[--surface] focus-visible:bg-[--surface] focus-visible:outline-none " +
          (selected ? "bg-[--surface]" : "")
        }
      >
        <div className="flex items-center gap-2">
          <span className="font-medium text-[--foreground-strong]">
            {entry.action}
          </span>
          <Badge tone="neutral">{entry.area || "—"}</Badge>
          <code className="truncate font-mono text-xs text-[--muted-foreground]">
            {entry.target}
          </code>
          <StatusPill tone={tone} className="ml-auto">
            {entry.outcome}
          </StatusPill>
          {undoable ? <Badge tone="info">↩ undo 가능</Badge> : null}
        </div>
        {before || after ? (
          <div className="flex items-center gap-2 pl-1 font-mono text-xs text-[--muted-foreground]">
            <span className="truncate">{before ?? "—"}</span>
            <span aria-hidden>→</span>
            <span className="truncate text-[--foreground]">{after ?? "—"}</span>
          </div>
        ) : null}
        <div className="flex items-center gap-3 pl-1 text-xs text-[--muted-foreground]">
          <span>{entry.actor_id || "system"}</span>
          <span aria-hidden>·</span>
          <span>{formatRelativeTs(entry.ts)}</span>
          {entry.trace_id ? (
            <>
              <span aria-hidden>·</span>
              <code className="font-mono">
                trace {entry.trace_id.slice(0, 8)}…
              </code>
            </>
          ) : null}
          {entry.requires_restart ? (
            <>
              <span aria-hidden>·</span>
              <span>재시작 필요</span>
            </>
          ) : null}
        </div>
      </button>
    </li>
  );
}

function EmptyState() {
  return (
    <section className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-[--radius-l] border border-dashed border-[--border-strong] bg-[--card] px-8 py-12 text-center">
      <h2 className="text-lg font-semibold text-[--foreground-strong]">
        조건에 맞는 감사 기록이 없어요
      </h2>
      <p className="text-sm text-[--muted-foreground]">
        필터를 줄이거나, 데몬에서 변경을 적용하면 여기 표시됩니다.
      </p>
    </section>
  );
}

function SkeletonList() {
  // 3행 정도의 시각 placeholder — fetch 1회 사이클(보통 100ms 이내)에만 보인다.
  return (
    <div className="flex flex-col gap-2 rounded-[--radius-l] border border-[--border] bg-[--card] p-4">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          aria-hidden
          className="h-12 animate-pulse rounded-[--radius-m] bg-[--surface]"
        />
      ))}
    </div>
  );
}

interface ErrorPanelProps {
  message: string;
  onRetry: () => void;
}

function ErrorPanel({ message, onRetry }: ErrorPanelProps) {
  return (
    <div
      role="alert"
      className="flex items-start justify-between gap-3 rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] p-3 text-sm text-[--color-error]"
    >
      <div>
        <p className="font-medium">감사 로그를 불러오지 못했어요.</p>
        <p className="mt-1 text-xs opacity-80">{message}</p>
      </div>
      <Button variant="ghost" size="sm" onClick={onRetry}>
        다시 시도
      </Button>
    </div>
  );
}
