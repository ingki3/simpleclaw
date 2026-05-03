"use client";

/**
 * Memory 화면 (BIZ-49) — admin.pen Screen 06 / DESIGN.md §4 Patterns.
 *
 * 구성:
 *  1. 헤더(타이틀 + Reload)
 *  2. 통계 3-카드(총 메시지·디스크·마지막 드리밍)
 *  3. 좌(2/3) — MEMORY.md 인덱스: 검색 + 타입 필터 + 가상 스크롤 항목 리스트
 *  4. 우(1/3) — 드리밍 트리거 + 진행 단계, 대화 내보내기(JSONL)
 *
 * 핵심 인터랙션:
 *  - 항목 편집: 인라인 textarea → 저장 → 5분 undo 토스트
 *  - 항목 삭제(영구): 행의 destructive-soft 색 ▸ ConfirmGate(파일명 "MEMORY.md" 입력 일치) ▸ 5분 undo 토스트
 *  - 드리밍 트리거: 진행 중 disable + 1.5초마다 상태 polling
 *  - 1000+ 인덱스: 가상 스크롤(VirtualList) — 100건 미만은 일반 렌더로 폴백
 *  - 내보내기: from/to 입력 → anchor download (JSONL streaming은 백엔드 일감)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Database, Download, RefreshCw, Search } from "lucide-react";
import { Badge, type BadgeTone } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { ConfirmGate } from "@/components/primitives/ConfirmGate";
import { useToast } from "@/components/primitives/Toast";
import { DreamingProgressCard } from "@/components/domain/DreamingProgressCard";
import { MemoryEntryRow } from "@/components/domain/MemoryEntryRow";
import { MemoryStatsCards } from "@/components/domain/MemoryStatsCards";
import { VirtualList } from "@/components/domain/VirtualList";
import {
  type DreamingState,
  type MemoryEntry,
  type MemoryEntryType,
  type MemoryIndexResponse,
  deleteMemoryEntry,
  exportConversationsUrl,
  getDreamingStatus,
  getMemoryIndex,
  patchMemoryEntry,
  triggerDreaming,
  undoMemoryChange,
} from "@/lib/api/memory";
import { cn } from "@/lib/cn";

const UNDO_WINDOW_MS = 5 * 60 * 1000;
const TYPE_FILTERS: ReadonlyArray<{
  value: MemoryEntryType | "all";
  label: string;
  tone: BadgeTone;
}> = [
  { value: "all", label: "전체", tone: "neutral" },
  { value: "user", label: "user", tone: "info" },
  { value: "feedback", label: "feedback", tone: "warning" },
  { value: "project", label: "project", tone: "brand" },
  { value: "reference", label: "reference", tone: "neutral" },
];

export default function MemoryPage() {
  const { push: pushToast } = useToast();

  const [data, setData] = useState<MemoryIndexResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<MemoryEntryType | "all">("all");
  const [pendingDelete, setPendingDelete] = useState<MemoryEntry | null>(null);
  const [exportRange, setExportRange] = useState<{ from: string; to: string }>({
    from: "",
    to: "",
  });

  const dreamingPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getMemoryIndex();
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 드리밍 진행 중에는 1.5초마다 상태 polling — running false가 되면 인덱스 재로드.
  useEffect(() => {
    if (!data?.dreaming.running) {
      if (dreamingPollRef.current) {
        clearInterval(dreamingPollRef.current);
        dreamingPollRef.current = null;
      }
      return;
    }
    if (dreamingPollRef.current) return;
    dreamingPollRef.current = setInterval(async () => {
      try {
        const next = await getDreamingStatus();
        setData((prev) => (prev ? { ...prev, dreaming: next } : prev));
        if (!next.running) {
          // 종료 직후 인덱스 재로드(요약이 추가됐을 수 있음).
          if (dreamingPollRef.current) {
            clearInterval(dreamingPollRef.current);
            dreamingPollRef.current = null;
          }
          void refresh();
        }
      } catch {
        // 일시적 실패는 무시 — 다음 tick에서 재시도.
      }
    }, 1500);
    return () => {
      if (dreamingPollRef.current) {
        clearInterval(dreamingPollRef.current);
        dreamingPollRef.current = null;
      }
    };
  }, [data?.dreaming.running, refresh]);

  // ---------------------------------------------------------------------
  // 인덱스 필터링
  // ---------------------------------------------------------------------

  const filtered = useMemo<MemoryEntry[]>(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.entries.filter((e) => {
      if (typeFilter !== "all" && e.type !== typeFilter) return false;
      if (q && !`${e.section} ${e.text}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, search, typeFilter]);

  // ---------------------------------------------------------------------
  // mutations
  // ---------------------------------------------------------------------

  const dreamingDisabled = !!data?.dreaming.running;

  const handleSaveEntry = async (id: string, text: string) => {
    try {
      const res = await patchMemoryEntry(id, text);
      setData((prev) => (prev ? { ...prev, entries: res.entries } : prev));
      pushToast({
        tone: "success",
        title: "항목을 저장했어요.",
        description: "5분 안에 되돌릴 수 있습니다.",
        undo: {
          label: "되돌리기 (5분)",
          expiresAt: Date.now() + UNDO_WINDOW_MS,
          onUndo: () => undoChange(res.undoToken),
        },
      });
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "저장에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
      throw e;
    }
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    try {
      const res = await deleteMemoryEntry(pendingDelete.id, "MEMORY.md");
      setData((prev) => (prev ? { ...prev, entries: res.entries } : prev));
      const removedText = pendingDelete.text;
      setPendingDelete(null);
      pushToast({
        tone: "destructive-soft",
        title: "항목을 영구 삭제했어요.",
        description:
          removedText.length > 60
            ? `“${removedText.slice(0, 60)}…”`
            : `“${removedText}”`,
        undo: {
          label: "되돌리기 (5분)",
          expiresAt: Date.now() + UNDO_WINDOW_MS,
          onUndo: () => undoChange(res.undoToken),
        },
      });
    } catch (e) {
      setPendingDelete(null);
      pushToast({
        tone: "destructive-soft",
        title: "삭제에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const undoChange = async (token: string) => {
    try {
      const res = await undoMemoryChange(token);
      setData((prev) => (prev ? { ...prev, entries: res.entries } : prev));
      pushToast({
        tone: "info",
        title: "변경을 되돌렸어요.",
        description: "MEMORY.md를 직전 상태로 복원했습니다.",
      });
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "되돌리기에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleTriggerDreaming = async () => {
    try {
      const res = await triggerDreaming();
      setData((prev) => (prev ? { ...prev, dreaming: res.state } : prev));
      pushToast({
        tone: "info",
        title: "드리밍을 시작했어요.",
        description: "진행 중에는 다시 누를 수 없어요.",
      });
    } catch (e) {
      pushToast({
        tone: "destructive-soft",
        title: "드리밍 트리거에 실패했어요.",
        description: e instanceof Error ? e.message : String(e),
      });
    }
  };

  // ---------------------------------------------------------------------
  // render
  // ---------------------------------------------------------------------

  if (loading && !data) {
    return (
      <div className="text-sm text-[--muted-foreground]">불러오는 중…</div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-[--radius-m] border border-[--color-error] bg-[--color-error-bg] px-4 py-3 text-sm">
        메모리 인덱스를 불러오지 못했습니다: {error}
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-[--foreground-strong]">
            <Database size={18} aria-hidden /> 기억
          </h1>
          <p className="mt-1 text-sm text-[--muted-foreground]">
            대화 저장소·MEMORY.md 인덱스를 살펴보고, 드리밍을 직접 트리거할 수 있어요.
          </p>
        </div>
        <Button
          variant="ghost"
          size="md"
          onClick={() => void refresh()}
          leftIcon={<RefreshCw size={14} aria-hidden />}
        >
          새로고침
        </Button>
      </header>

      <MemoryStatsCards stats={data.stats} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* 인덱스 (좌측 2/3) */}
        <section
          aria-labelledby="memory-index-title"
          className="flex flex-col gap-3 rounded-[--radius-l] border border-[--border] bg-[--card] p-5 lg:col-span-2"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2
                id="memory-index-title"
                className="text-sm font-semibold text-[--foreground-strong]"
              >
                MEMORY.md 인덱스
              </h2>
              <p className="text-xs text-[--muted-foreground]">
                {data.entries.length.toLocaleString()}개 항목 ·{" "}
                {(data.file.sizeBytes / 1024).toFixed(1)} KB
                {data.file.updatedAt
                  ? ` · 수정: ${new Date(data.file.updatedAt).toLocaleString("ko-KR")}`
                  : ""}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search
                  size={14}
                  aria-hidden
                  className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[--muted-foreground]"
                />
                <Input
                  type="search"
                  placeholder="검색"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  aria-label="항목 검색"
                  className="pl-7"
                />
              </div>
            </div>
          </div>

          {/* 타입 필터 칩 */}
          <div role="tablist" aria-label="타입 필터" className="flex flex-wrap gap-1.5">
            {TYPE_FILTERS.map((f) => {
              const active = typeFilter === f.value;
              const count =
                f.value === "all"
                  ? data.entries.length
                  : data.entries.filter((e) => e.type === f.value).length;
              return (
                <button
                  key={f.value}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setTypeFilter(f.value)}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-[--radius-pill] border px-2.5 py-1 text-xs font-medium transition-colors",
                    active
                      ? "border-[--primary] bg-[--primary-tint] text-[--primary]"
                      : "border-[--border-divider] bg-[--surface] text-[--muted-foreground] hover:text-[--foreground]",
                  )}
                >
                  <span>{f.label}</span>
                  <Badge tone={active ? "brand" : "neutral"}>{count}</Badge>
                </button>
              );
            })}
          </div>

          {/* 항목 리스트 */}
          {filtered.length === 0 ? (
            <div className="rounded-[--radius-m] border border-dashed border-[--border-divider] bg-[--surface] px-4 py-8 text-center text-xs text-[--muted-foreground]">
              {data.entries.length === 0
                ? "아직 MEMORY.md 항목이 없어요. 드리밍을 한 번 돌려 봐요."
                : "필터에 해당하는 항목이 없어요."}
            </div>
          ) : (
            <VirtualList
              items={filtered}
              estimatedRowHeight={72}
              threshold={100}
              maxHeight={560}
              className="rounded-[--radius-m] border border-[--border-divider] bg-[--surface]"
              renderItem={(entry) => (
                <MemoryEntryRow
                  key={entry.id}
                  entry={entry}
                  disabled={dreamingDisabled}
                  onSave={handleSaveEntry}
                  onRequestDelete={(e) => setPendingDelete(e)}
                />
              )}
            />
          )}
        </section>

        {/* 우측 1/3 — 드리밍 + 내보내기 */}
        <aside className="flex flex-col gap-4">
          <DreamingProgressCard
            state={data.dreaming}
            onTrigger={handleTriggerDreaming}
            disabled={false}
          />
          <ExportCard
            range={exportRange}
            onChange={setExportRange}
            disabled={dreamingDisabled}
          />
        </aside>
      </div>

      <ConfirmGate
        open={!!pendingDelete}
        onOpenChange={(o) => {
          if (!o) setPendingDelete(null);
        }}
        title="이 항목을 영구 삭제할까요?"
        description={
          <>
            지금 항목을 MEMORY.md에서 영구히 제거합니다. 5분 안에는 되돌릴 수
            있어요. 계속하려면{" "}
            <span className="font-mono">MEMORY.md</span>를 그대로 입력해 주세요.
          </>
        }
        confirmation="MEMORY.md"
        confirmLabel="영구 삭제"
        onConfirm={handleConfirmDelete}
      >
        {pendingDelete ? (
          <div className="rounded-[--radius-m] border border-[--border-divider] bg-[--surface] px-3 py-2 text-xs text-[--foreground]">
            <div className="text-[10px] font-mono text-[--muted-foreground]">
              {pendingDelete.section} · {pendingDelete.id}
            </div>
            <div className="mt-1 break-words">{pendingDelete.text}</div>
          </div>
        ) : null}
      </ConfirmGate>
    </div>
  );
}

// ---------------------------------------------------------------------
// 내보내기 카드 — 기간 선택 + JSONL 다운로드 anchor
// ---------------------------------------------------------------------

interface ExportRange {
  from: string;
  to: string;
}

interface ExportCardProps {
  range: ExportRange;
  onChange: (next: ExportRange) => void;
  disabled?: boolean;
}

function ExportCard({ range, onChange, disabled }: ExportCardProps) {
  // input[type=date]는 브라우저별로 ISO 형식이 다를 수 있어 그대로 백엔드에 전달.
  // 빈 값은 무제한으로 해석된다.
  const href = exportConversationsUrl(
    range.from || null,
    range.to ? `${range.to}T23:59:59` : null,
  );
  return (
    <section
      aria-labelledby="export-card-title"
      className="flex flex-col gap-3 rounded-[--radius-l] border border-[--border] bg-[--card] p-5"
    >
      <header>
        <h2
          id="export-card-title"
          className="flex items-center gap-2 text-sm font-semibold text-[--foreground-strong]"
        >
          <Download size={14} aria-hidden /> 대화 내보내기 (JSONL)
        </h2>
        <p className="mt-1 text-xs text-[--muted-foreground]">
          기간을 비워 두면 전체 대화가 내려와요. UTC 기준 ISO 시각을 사용합니다.
        </p>
      </header>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-xs text-[--muted-foreground]">
          <span>From</span>
          <Input
            type="date"
            value={range.from}
            onChange={(e) => onChange({ ...range, from: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[--muted-foreground]">
          <span>To</span>
          <Input
            type="date"
            value={range.to}
            onChange={(e) => onChange({ ...range, to: e.target.value })}
          />
        </label>
      </div>
      <a
        href={disabled ? undefined : href}
        download
        aria-disabled={disabled}
        className={cn(
          "inline-flex items-center justify-center gap-2 rounded-[--radius-m] px-4 py-2 text-sm font-medium transition-colors",
          disabled
            ? "pointer-events-none bg-[--surface] text-[--muted-foreground]"
            : "bg-[--primary] text-[--primary-foreground] hover:bg-[--primary-hover]",
        )}
      >
        <Download size={14} aria-hidden />
        {disabled ? "드리밍 중에는 내보낼 수 없어요" : "JSONL 다운로드"}
      </a>
    </section>
  );
}
