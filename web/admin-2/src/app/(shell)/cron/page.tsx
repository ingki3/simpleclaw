/**
 * /cron — Admin 2.0 S7 (BIZ-118).
 *
 * admin.pen `euRDL` (Cron Shell) 의 콘텐츠 영역을 React 로 박제한다.
 * 구성 (위 → 아래):
 *  1) 페이지 헤더 — h1 "크론" + 한 줄 설명 + 검색 입력 + "+ 새 작업" 버튼.
 *  2) CronJobsList — 잡 목록 테이블 (CronJobRow reusable).
 *     `?jobs=loading|empty|error` 쿼리로 4-variant 검증.
 *  3) 실행 히스토리 카드 — 24h 성공률·평균 시간·circuit-open·재시도.
 *  4) NewCronJobModal — cron expression 검증 + DryRunCard 미리보기.
 *
 * 본 단계는 정적 fixture 만 사용 — 데이터 소스 연결은 데몬 통합 단계에서 교체.
 * 토글/추가는 로컬 상태만 갱신하고 console 로 박제 (실제 mutation 은 후속 sub-issue).
 */
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { findAreaByPath } from "@/app/areas";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { MetricCard } from "@/design/molecules/MetricCard";
import {
  CronJobsList,
  type CronJobsListState,
} from "./_components/CronJobsList";
import {
  NewCronJobModal,
  type NewCronJobInput,
} from "./_components/NewCronJobModal";
import { getCronSnapshot, type CronJob } from "./_data";

const VALID_LIST_STATES: readonly CronJobsListState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export default function CronPage() {
  return (
    <Suspense fallback={null}>
      <CronContent />
    </Suspense>
  );
}

function CronContent() {
  const area = findAreaByPath("/cron");
  const snapshot = useMemo(() => getCronSnapshot(), []);

  // 4-variant 쿼리 — `?jobs=` 만 단일 매개변수.
  const params = useSearchParams();
  const jobsState = readState(params.get("jobs"));

  // 로컬 상태 — fixture 를 mutable 카피로 들고 있으면서 토글/추가가 즉시 반영되도록.
  const [jobs, setJobs] = useState<CronJob[]>(() => [...snapshot.jobs]);
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  // empty variant 일 때는 fixture 를 비워서 EmptyState 가 노출되도록 — variant 검증.
  const jobsForRender =
    jobsState === "empty" ? [] : applyFilter(jobs, search);

  // 페이지 헤더 우측 카운트 — "실행 N · 일시정지 N · circuit-open N".
  const counts = useMemo(() => summarizeCounts(jobs), [jobs]);

  // 24h 통계 — fixture 의 history 를 그대로 노출 (데몬 연결 시 실시간 교체).
  const history = snapshot.history;
  const successRate =
    history.totalRuns === 0
      ? 0
      : Math.round((history.success / history.totalRuns) * 1000) / 10;
  const avgSeconds = Math.round((history.averageMs / 1000) * 10) / 10;

  return (
    <section
      className="mx-auto flex max-w-6xl flex-col gap-6 p-8"
      data-testid="cron-page"
    >
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold text-(--foreground-strong)">
              {area?.label ?? "크론"}
            </h1>
            <p className="text-sm text-(--muted-foreground)">
              스케줄 작업의 다음 실행·상태·circuit breaker 를 관리합니다 (DESIGN.md §3.4).
            </p>
          </div>
          <div
            data-testid="cron-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="success">실행 {counts.running}</Badge>
            <Badge tone="neutral">일시정지 {counts.paused}</Badge>
            <Badge
              tone={counts.circuitOpen > 0 ? "danger" : "neutral"}
            >
              circuit-open {counts.circuitOpen}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-[260px] flex-1">
            <Input
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              placeholder="이름·스케줄로 검색"
              leading={<span aria-hidden>⌕</span>}
              data-testid="cron-search"
            />
          </div>
          <Button
            variant="primary"
            onClick={() => setCreateOpen(true)}
            data-testid="cron-create"
          >
            ＋ 새 작업
          </Button>
        </div>
      </header>

      <SectionHeader
        id="jobs"
        title={`등록된 작업 (${jobs.length})`}
        subtitle="이름 · 스케줄 · 다음 실행 · 상태 · circuit · 활성."
      />
      <CronJobsList
        state={jobsState}
        jobs={jobsForRender}
        searchQuery={search}
        onToggleEnabled={(id, next) => {
          setJobs((cur) =>
            cur.map((j) => (j.id === id ? { ...j, enabled: next } : j)),
          );
          if (typeof console !== "undefined") {
            console.info("[cron] toggle job", id, next);
          }
        }}
        onRunNow={(id) => {
          if (typeof console !== "undefined") {
            console.info("[cron] run now", id);
          }
        }}
        onCreate={() => setCreateOpen(true)}
        onRetry={() => {
          if (typeof console !== "undefined") {
            console.info("[cron] retry jobs fetch");
          }
        }}
      />

      <SectionHeader
        id="history"
        title="실행 히스토리 · 최근 24h"
        subtitle={`${history.totalRuns}회 실행 · 성공 ${history.success} · 실패 ${history.failure} · 평균 ${avgSeconds}s.`}
      />
      <div
        data-testid="cron-history"
        className="grid grid-cols-2 gap-4 lg:grid-cols-4"
      >
        <MetricCard label="성공률" value={`${successRate}%`} />
        <MetricCard label="평균 실행시간" value={`${avgSeconds}s`} />
        <MetricCard
          label="circuit-open"
          value={String(history.circuitOpen)}
          deltaTone={history.circuitOpen > 0 ? "negative" : "neutral"}
        />
        <MetricCard label="재시도" value={String(history.retries)} />
      </div>

      <NewCronJobModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={(input) => {
          setJobs((cur) => [...cur, materialize(input)]);
          if (typeof console !== "undefined") {
            console.info("[cron] create job", input.name, input.schedule);
          }
        }}
      />
    </section>
  );
}

function SectionHeader({
  id,
  title,
  subtitle,
}: {
  id: string;
  title: string;
  subtitle: string;
}) {
  return (
    <header className="flex flex-col gap-0.5" id={id} data-testid={`section-${id}`}>
      <h2 className="text-base font-semibold text-(--foreground-strong)">
        {title}
      </h2>
      <p className="text-xs text-(--muted-foreground)">{subtitle}</p>
    </header>
  );
}

/** ?jobs=foo 의 4-variant 매핑 — 알 수 없으면 default. */
function readState(raw: string | null): CronJobsListState {
  if (raw && (VALID_LIST_STATES as readonly string[]).includes(raw)) {
    return raw as CronJobsListState;
  }
  return "default";
}

interface JobNameSchedule {
  name: string;
  schedule: string;
}

/** 검색 — name/schedule substring. */
function applyFilter<T extends JobNameSchedule>(
  items: readonly T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...items];
  return items.filter((item) => {
    if (item.name.toLowerCase().includes(q)) return true;
    if (item.schedule.toLowerCase().includes(q)) return true;
    return false;
  });
}

interface Counts {
  running: number;
  paused: number;
  circuitOpen: number;
}

function summarizeCounts(jobs: readonly CronJob[]): Counts {
  let running = 0;
  let paused = 0;
  let circuitOpen = 0;
  for (const j of jobs) {
    if (!j.enabled) paused += 1;
    else running += 1;
    if (j.circuit === "open") circuitOpen += 1;
  }
  return { running, paused, circuitOpen };
}

/** 모달 입력 → fixture 카드. enabled 상태와 placeholder lastRun 으로 채운다. */
function materialize(input: NewCronJobInput): CronJob {
  return {
    id: input.name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-"),
    name: input.name,
    schedule: input.scheduleRaw || input.schedule,
    skillId: input.skillId,
    payload: input.payload,
    timeoutSeconds: input.timeoutSeconds,
    maxRetries: input.maxRetries,
    enabled: input.enabled,
    lastRun: null,
    health: "healthy",
    circuit: "closed",
  };
}
