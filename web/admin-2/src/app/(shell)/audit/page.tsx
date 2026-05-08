/**
 * /audit — Admin 2.0 S12 (BIZ-123).
 *
 * admin.pen `Auu2Y` (Audit Shell) · `nHHuf` (변경 되돌리기 modal, BIZ-109 P1) 의
 * 콘텐츠 영역을 React 로 박제한다. 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "감사" + 한 줄 설명 + outcome 카운트 배지.
 *  2) 6개 필터 — 검색 / 영역 / 액션 / 액터 / 기간 / "실패만" 토글.
 *  3) AuditList — AuditEntry molecule 행 + 4-variant + Undo 액션.
 *     `?audit=loading|empty|error` 쿼리로 4-variant 검증.
 *  4) UndoConfirmModal — `nHHuf` 시각 spec.
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 데몬 통합 단계에서 교체.
 * Undo 확정 mutation 은 console 로 박제 (실제 daemon 호출은 후속 sub-issue).
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { Input } from "@/design/atoms/Input";
import { Select } from "@/design/atoms/Select";
import { Switch } from "@/design/atoms/Switch";
import { AuditList, type AuditListState } from "./_components/AuditList";
import { UndoConfirmModal } from "./_components/UndoConfirmModal";
import {
  ACTION_OPTIONS,
  AREA_OPTIONS,
  TIME_RANGE_OPTIONS,
  applyAuditFilter,
  findEntryById,
  getAuditSnapshot,
  listActors,
  type AuditAction,
  type AuditArea,
  type AuditEntry,
  type AuditTimeRange,
} from "./_data";

const VALID_LIST_STATES: readonly AuditListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function AuditPage() {
  return (
    <Suspense fallback={null}>
      <AuditContent />
    </Suspense>
  );
}

function AuditContent() {
  const area = findAreaByPath("/audit");
  const snapshot = useMemo(() => getAuditSnapshot(), []);

  // 4-variant 쿼리 — `?audit=` 만 단일 매개변수.
  const params = useSearchParams();
  const listState = readState(params.get("audit"));

  // 6개 필터의 로컬 상태 — 페이지가 SSOT.
  const [query, setQuery] = useState("");
  const [areaFilter, setAreaFilter] = useState<AuditArea | "all">("all");
  const [actionFilter, setActionFilter] = useState<AuditAction | "all">("all");
  const [actor, setActor] = useState<string | "all">("all");
  const [range, setRange] = useState<AuditTimeRange>("30d");
  const [failedOnly, setFailedOnly] = useState(false);

  // Undo 클릭 → 모달 오픈.
  const [activeEntry, setActiveEntry] = useState<AuditEntry | null>(null);

  // empty variant 일 때는 fixture 를 비워서 EmptyState 가 노출되도록 — variant 검증.
  const filtered = useMemo(
    () =>
      listState === "empty"
        ? []
        : applyAuditFilter(snapshot.entries, {
            query,
            area: areaFilter,
            action: actionFilter,
            actor,
            range,
            failedOnly,
            // fixture 가 고정 시각이라 마지막 entry 기준으로 잘라야 결정적.
            now: undefined,
          }),
    [
      snapshot.entries,
      query,
      areaFilter,
      actionFilter,
      actor,
      range,
      failedOnly,
      listState,
    ],
  );

  // 헤더 카운트 — failed/applied 는 운영 신호로 강조.
  const counts = useMemo(() => summarizeOutcomes(filtered), [filtered]);

  // 액터 드롭다운 옵션 — 전체 픽스처 기준.
  const actorOptions = useMemo(() => {
    const actors = listActors(snapshot.entries);
    return [
      { value: "all", label: "전체 액터" },
      ...actors.map((a) => ({ value: a, label: a })),
    ];
  }, [snapshot.entries]);

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="audit-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "감사"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              admin 2 의 모든 변경을 액션 단위로 기록하고 임의 시점 되돌리기를 다룹니다 (DESIGN.md §4.4).
            </p>
          </div>
          <div
            data-testid="audit-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="info">전체 {filtered.length}</Badge>
            <Badge tone={counts.applied > 0 ? "success" : "neutral"}>
              applied {counts.applied}
            </Badge>
            <Badge tone={counts.failed > 0 ? "danger" : "neutral"}>
              failed {counts.failed}
            </Badge>
            <Badge tone={counts.rolledBack > 0 ? "warning" : "neutral"}>
              rolled-back {counts.rolledBack}
            </Badge>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <div className="sm:col-span-4">
            <Input
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              placeholder="actor / target / action 검색"
              leading={<span aria-hidden>⌕</span>}
              data-testid="audit-search"
            />
          </div>
          <div className="sm:col-span-2">
            <Select
              value={areaFilter}
              onChange={(e) =>
                setAreaFilter(e.currentTarget.value as AuditArea | "all")
              }
              options={AREA_OPTIONS.map((o) => ({
                value: o.value,
                label: o.label,
              }))}
              data-testid="audit-area"
            />
          </div>
          <div className="sm:col-span-2">
            <Select
              value={actionFilter}
              onChange={(e) =>
                setActionFilter(e.currentTarget.value as AuditAction | "all")
              }
              options={ACTION_OPTIONS.map((o) => ({
                value: o.value,
                label: o.label,
              }))}
              data-testid="audit-action"
            />
          </div>
          <div className="sm:col-span-2">
            <Select
              value={actor}
              onChange={(e) => setActor(e.currentTarget.value)}
              options={actorOptions}
              data-testid="audit-actor"
            />
          </div>
          <div className="sm:col-span-2">
            <Select
              value={range}
              onChange={(e) =>
                setRange(e.currentTarget.value as AuditTimeRange)
              }
              options={TIME_RANGE_OPTIONS.map((o) => ({
                value: o.value,
                label: o.label,
              }))}
              data-testid="audit-range"
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 text-xs text-(--muted-foreground)">
          <label className="flex items-center gap-2">
            <span>실패만 보기</span>
            <Switch
              checked={failedOnly}
              onCheckedChange={setFailedOnly}
              data-testid="audit-failed-only"
              label="실패만 보기"
            />
          </label>
        </div>
      </header>

      <AuditList
        state={listState}
        entries={filtered}
        searchQuery={query}
        onUndo={(entry) => {
          // applied 가 아닌 행은 AuditList 에서 비활성 — 도달 시 무시.
          const target = findEntryById(snapshot.entries, entry.id);
          if (target) setActiveEntry(target);
        }}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[audit] retry entries fetch");
          }
        }}
      />

      <UndoConfirmModal
        open={activeEntry !== null}
        entry={activeEntry}
        onClose={() => setActiveEntry(null)}
        onConfirm={(entry) => {
          if (typeof console !== "undefined") {
            console.info("[audit] undo confirmed", entry.id);
          }
          setActiveEntry(null);
        }}
      />
    </section>
  );
}

/** ?audit=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState(raw: string | null): AuditListState {
  if (raw && (VALID_LIST_STATES as readonly string[]).includes(raw)) {
    return raw as AuditListState;
  }
  return "default";
}

interface OutcomeCounts {
  applied: number;
  failed: number;
  rolledBack: number;
}

function summarizeOutcomes(entries: readonly AuditEntry[]): OutcomeCounts {
  let applied = 0;
  let failed = 0;
  let rolledBack = 0;
  for (const e of entries) {
    if (e.outcome === "applied") applied += 1;
    if (e.outcome === "failed") failed += 1;
    if (e.outcome === "rolled-back") rolledBack += 1;
  }
  return { applied, failed, rolledBack };
}
