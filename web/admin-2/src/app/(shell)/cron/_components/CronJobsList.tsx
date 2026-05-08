/**
 * CronJobsList — admin.pen `euRDL` "등록된 작업" 테이블 + 4-variant.
 *
 * Domain reusable `CronJobRow` 를 한 줄에 한 잡씩 배치한다.
 * DESIGN.md §1 Principle 3 — default/empty/loading/error 4-variant 시각 박제.
 *
 * skills-recipes 의 SkillsList 와 동일한 데이터 흐름:
 *   - 부모(page) 가 mutation 을 담당 — `onToggleEnabled` 만 콜백.
 *   - `searchQuery` 가 있으면 필터된 결과의 빈 상태를 다른 문구로 안내.
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { Switch } from "@/design/atoms/Switch";
import { CronJobRow } from "@/design/domain/CronJobRow";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { CronJob } from "../_data";

export type CronJobsListState = "default" | "empty" | "loading" | "error";

interface CronJobsListProps {
  state: CronJobsListState;
  jobs?: readonly CronJob[];
  /** Switch — 부모가 fixture/state 갱신을 담당. */
  onToggleEnabled: (id: string, next: boolean) => void;
  /** 즉시 실행 — 본 단계는 stub (console). */
  onRunNow?: (id: string) => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  /** "+ 새 작업" 트리거 — empty CTA 도 동일 액션. */
  onCreate?: () => void;
  /** 검색어 — 필터된 빈 결과 안내에 사용. */
  searchQuery?: string;
  className?: string;
}

const SKELETON_COUNT = 4;

export function CronJobsList({
  state,
  jobs = [],
  onToggleEnabled,
  onRunNow,
  errorMessage = "크론 잡 목록을 불러오지 못했습니다.",
  onRetry,
  onCreate,
  searchQuery,
  className,
}: CronJobsListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="cron-jobs-list"
      data-state={state}
      aria-label="등록된 크론 잡"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? (
        <ListEmpty onCreate={onCreate} filtered={false} />
      ) : null}
      {state === "default" ? (
        jobs.length === 0 ? (
          <ListEmpty onCreate={onCreate} filtered={isFiltered} />
        ) : (
          <Table
            jobs={jobs}
            onToggleEnabled={onToggleEnabled}
            onRunNow={onRunNow}
          />
        )
      ) : null}
    </section>
  );
}

function Table({
  jobs,
  onToggleEnabled,
  onRunNow,
}: {
  jobs: readonly CronJob[];
  onToggleEnabled: (id: string, next: boolean) => void;
  onRunNow?: (id: string) => void;
}) {
  return (
    <div className="overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)">
      <table
        data-testid="cron-jobs-table"
        className="w-full table-fixed border-collapse text-left"
      >
        <colgroup>
          <col style={{ width: "22%" }} />
          <col style={{ width: "16%" }} />
          <col style={{ width: "18%" }} />
          <col style={{ width: "12%" }} />
          <col style={{ width: "12%" }} />
          <col style={{ width: "20%" }} />
        </colgroup>
        <thead className="border-b border-(--border) bg-(--surface) text-xs uppercase tracking-wide text-(--muted-foreground)">
          <tr>
            <th className="px-3 py-2 font-medium">이름</th>
            <th className="px-3 py-2 font-medium">스케줄</th>
            <th className="px-3 py-2 font-medium">다음 실행</th>
            <th className="px-3 py-2 font-medium">상태</th>
            <th className="px-3 py-2 font-medium">circuit</th>
            <th className="px-3 py-2 text-right font-medium">활성</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <CronJobRow
              key={job.id}
              name={job.name}
              schedule={job.schedule}
              nextRun={formatNextRun(job)}
              status={statusForRow(job)}
              circuit={job.circuit}
              actions={
                <div
                  className="flex items-center justify-end gap-2"
                  data-testid={`cron-job-${job.id}-actions`}
                >
                  {onRunNow ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onRunNow(job.id)}
                      data-testid={`cron-job-${job.id}-run`}
                    >
                      실행
                    </Button>
                  ) : null}
                  <Switch
                    checked={job.enabled}
                    onCheckedChange={(next) => onToggleEnabled(job.id, next)}
                    label={`${job.name} 활성화`}
                    data-testid={`cron-job-${job.id}-toggle`}
                  />
                </div>
              }
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * 행의 status 결정 — circuit-open 이면 항상 failed 로 표기 (운영자에게 더 강한 신호).
 * 그 외에는 last-run 의 상태를 그대로.
 */
function statusForRow(job: CronJob): "idle" | "running" | "success" | "failed" {
  if (job.circuit === "open") return "failed";
  if (!job.lastRun) return "idle";
  return job.lastRun.status;
}

/** 다음 실행 시각 — 미리보기 단계는 schedule + 마지막 실행을 한 줄로. */
function formatNextRun(job: CronJob): string {
  if (!job.enabled) return "비활성";
  if (job.circuit === "open") return "circuit-open · 차단됨";
  // S7 단계는 데몬 미연결 — 표현식 자체를 다음 실행 힌트로 노출.
  return `${job.schedule}`;
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="크론 잡 목록 로딩 중"
      data-testid="cron-jobs-list-loading"
      className="overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card)"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse items-center gap-4 border-b border-(--border) px-3 py-4 last:border-b-0"
        >
          <div className="h-4 w-32 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-4 w-24 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-4 w-28 rounded-(--radius-sm) bg-(--surface)" />
          <div className="ml-auto h-5 w-10 rounded-(--radius-pill) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({
  onCreate,
  filtered,
}: {
  onCreate?: () => void;
  filtered: boolean;
}) {
  if (filtered) {
    return (
      <div data-testid="cron-jobs-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="검색 결과가 없어요"
          description="다른 키워드로 다시 시도하거나, 새 잡을 추가하세요."
          action={
            onCreate ? (
              <Button size="sm" variant="secondary" onClick={onCreate}>
                새 작업 추가
              </Button>
            ) : null
          }
        />
      </div>
    );
  }
  return (
    <div data-testid="cron-jobs-list-empty" data-empty-reason="none">
      <EmptyState
        title="등록된 크론 잡이 없어요"
        description="자주 쓰는 스킬을 정해진 시각에 자동 실행하세요. 5분 단위 검사부터 시작해도 좋습니다."
        action={
          onCreate ? (
            <Button size="sm" variant="primary" onClick={onCreate}>
              ＋ 새 작업
            </Button>
          ) : null
        }
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
      data-testid="cron-jobs-list-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
        <Badge tone="danger" size="sm">cron</Badge>
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
          data-testid="cron-jobs-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
