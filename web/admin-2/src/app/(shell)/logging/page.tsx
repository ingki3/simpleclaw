/**
 * /logging — Admin 2.0 S11 (BIZ-122).
 *
 * admin.pen `kGAWN` (Logging & Traces Shell) · `EvyYa` (Trace Detail) 의 콘텐츠 영역을
 * React 로 박제한다. 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "로그" + 한 줄 설명 + 카운트 배지.
 *  2) 4개 필터 — 검색 / 레벨 / 시간 범위 / 서비스.
 *  3) TracesList — 표 + 4-variant + 행 클릭 → Trace Detail.
 *     `?traces=loading|empty|error` 쿼리로 4-variant 검증.
 *  4) TraceDetailModal — `EvyYa` 시각 spec.
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 데몬 통합 단계에서 교체.
 * 행 클릭 → 모달 오픈 외 mutation 은 console 로 박제.
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { Input } from "@/design/atoms/Input";
import { Select } from "@/design/atoms/Select";
import { TracesList, type TracesListState } from "./_components/TracesList";
import { TraceDetailModal } from "./_components/TraceDetailModal";
import {
  applyLogFilter,
  findTraceById,
  getLoggingSnapshot,
  LEVEL_OPTIONS,
  listServices,
  TIME_RANGE_OPTIONS,
  type LogEvent,
  type LogLevel,
  type LogTimeRange,
  type TraceDetail,
} from "./_data";

const VALID_LIST_STATES: readonly TracesListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function LoggingPage() {
  return (
    <Suspense fallback={null}>
      <LoggingContent />
    </Suspense>
  );
}

function LoggingContent() {
  const area = findAreaByPath("/logging");
  const snapshot = useMemo(() => getLoggingSnapshot(), []);

  // 4-variant 쿼리 — `?traces=` 만 단일 매개변수.
  const params = useSearchParams();
  const tracesState = readState(params.get("traces"));

  // 4개 필터의 로컬 상태 — 페이지가 SSOT.
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState<LogLevel | "all">("all");
  const [range, setRange] = useState<LogTimeRange>("1h");
  const [service, setService] = useState<string | "all">("all");

  // 행 클릭 → 모달 오픈. trace 미발견은 그냥 무시.
  const [activeTrace, setActiveTrace] = useState<TraceDetail | null>(null);

  // empty variant 일 때는 fixture 를 비워서 EmptyState 가 노출되도록 — variant 검증.
  const filtered = useMemo(
    () =>
      tracesState === "empty"
        ? []
        : applyLogFilter(snapshot.events, {
            query,
            level,
            service,
            range,
            // fixture 가 고정 시각이라 마지막 이벤트 기준으로 잘라야 결정적.
            now: undefined,
          }),
    [snapshot.events, query, level, service, range, tracesState],
  );

  // 헤더 카운트 — error/warn 은 운영 신호로 강조.
  const counts = useMemo(() => summarizeLevels(filtered), [filtered]);

  // 서비스 드롭다운 옵션 — 전체 픽스처 기준 (필터 결과로 좁히면 자기 자신을 잃음).
  const serviceOptions = useMemo(() => {
    const services = listServices(snapshot.events);
    return [
      { value: "all", label: "전체 서비스" },
      ...services.map((s) => ({ value: s, label: s })),
    ];
  }, [snapshot.events]);

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="logging-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "로그"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              구조화 로그 · 메트릭 · trace timeline 을 봅니다 (DESIGN.md §3.4).
            </p>
          </div>
          <div
            data-testid="logging-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="info">전체 {filtered.length}</Badge>
            <Badge tone={counts.warn > 0 ? "warning" : "neutral"}>
              warn {counts.warn}
            </Badge>
            <Badge tone={counts.error > 0 ? "danger" : "neutral"}>
              error {counts.error}
            </Badge>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <div className="sm:col-span-5">
            <Input
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              placeholder="메시지·서비스 검색"
              leading={<span aria-hidden>⌕</span>}
              data-testid="logging-search"
            />
          </div>
          <div className="sm:col-span-2">
            <Select
              value={level}
              onChange={(e) => setLevel(e.currentTarget.value as LogLevel | "all")}
              options={LEVEL_OPTIONS.map((o) => ({
                value: o.value,
                label: o.label,
              }))}
              data-testid="logging-level"
            />
          </div>
          <div className="sm:col-span-2">
            <Select
              value={range}
              onChange={(e) => setRange(e.currentTarget.value as LogTimeRange)}
              options={TIME_RANGE_OPTIONS.map((o) => ({
                value: o.value,
                label: o.label,
              }))}
              data-testid="logging-range"
            />
          </div>
          <div className="sm:col-span-3">
            <Select
              value={service}
              onChange={(e) => setService(e.currentTarget.value)}
              options={serviceOptions}
              data-testid="logging-service"
            />
          </div>
        </div>
      </header>

      <TracesList
        state={tracesState}
        events={filtered}
        searchQuery={query}
        onSelect={(event: LogEvent) => {
          if (!event.traceId) return;
          const trace = findTraceById(snapshot.traces, event.traceId);
          if (trace) {
            setActiveTrace(trace);
          } else if (typeof console !== "undefined") {
            console.warn("[logging] trace not found", event.traceId);
          }
        }}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[logging] retry events fetch");
          }
        }}
      />

      <TraceDetailModal
        open={activeTrace !== null}
        trace={activeTrace}
        onClose={() => setActiveTrace(null)}
      />
    </section>
  );
}

/** ?traces=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState(raw: string | null): TracesListState {
  if (raw && (VALID_LIST_STATES as readonly string[]).includes(raw)) {
    return raw as TracesListState;
  }
  return "default";
}

interface LevelCounts {
  warn: number;
  error: number;
}

function summarizeLevels(events: readonly LogEvent[]): LevelCounts {
  let warn = 0;
  let error = 0;
  for (const e of events) {
    if (e.level === "warn") warn += 1;
    if (e.level === "error") error += 1;
  }
  return { warn, error };
}
