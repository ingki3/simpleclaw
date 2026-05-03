"use client";

/**
 * Cron 화면 — admin.pen Screen 05.
 *
 * BIZ-43(공통 API 클라이언트 + primitives)이 합류하기 전 단계라, 본 페이지는
 * ``_primitives/`` 와 ``_components/``에 자기-완결 컴포넌트를 두고 mock 클라이언트
 * (``@/lib/cron/client``)로 동작한다. BIZ-43 머지 후에는:
 *  - ``_primitives/*``  → ``@/components/primitives/*``
 *  - mock 클라이언트의 내부를 ``fetchAdmin('/admin/v1/cron/...')`` 호출로 교체
 *
 * 페이지 컨테이너 자체는 변경 불요.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { CronJobsTable } from "./_components/CronJobsTable";
import { NewJobModal } from "./_components/NewJobModal";
import { RunHistoryDrawer } from "./_components/RunHistoryDrawer";
import { ConfirmGate } from "./_primitives/ConfirmGate";
import { ToastProvider, useToast } from "./_primitives/Toast";
import {
  deleteCronJob,
  listCronJobs,
  runCronJobNow,
  updateCronJob,
} from "@/lib/cron/client";
import type { CronJob } from "@/lib/cron/types";

export default function CronPage() {
  // ToastProvider는 페이지 안에서 한 번만 마운트 — 다른 화면에 영향 없음.
  return (
    <ToastProvider>
      <CronPageBody />
    </ToastProvider>
  );
}

type StatusFilter = "all" | "enabled" | "paused" | "circuit_open";

function CronPageBody() {
  const toast = useToast();
  const [jobs, setJobs] = useState<CronJob[] | null>(null);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [showNew, setShowNew] = useState(false);
  const [selected, setSelected] = useState<CronJob | null>(null);
  // Drawer 안에서 새 실행 발생 시 강제 재조회를 트리거하는 키.
  const [runsRefresh, setRunsRefresh] = useState(0);
  const [pendingRunNow, setPendingRunNow] = useState<CronJob | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CronJob | null>(null);

  const refresh = useCallback(async () => {
    const list = await listCronJobs();
    setJobs(list);
    // 선택된 잡이 갱신되면 Drawer 헤더도 최신 데이터로.
    setSelected((cur) =>
      cur ? list.find((j) => j.name === cur.name) ?? null : null,
    );
  }, []);

  useEffect(() => {
    refresh().catch((e) => {
      toast.show("error", `잡 목록을 불러오지 못했어요 — ${describe(e)}`);
    });
  }, [refresh, toast]);

  const filtered = useMemo(() => {
    if (!jobs) return [];
    return jobs.filter((j) => {
      if (search && !j.name.toLowerCase().includes(search.toLowerCase()))
        return false;
      switch (statusFilter) {
        case "enabled":
          return j.enabled;
        case "paused":
          return !j.enabled;
        case "circuit_open":
          return (
            j.circuitBreakThreshold > 0 &&
            j.consecutiveFailures >= j.circuitBreakThreshold
          );
        default:
          return true;
      }
    });
  }, [jobs, search, statusFilter]);

  const onToggleEnabled = async (job: CronJob, next: boolean) => {
    setBusyJob(job.name);
    try {
      await updateCronJob(job.name, { enabled: next });
      await refresh();
      toast.show("success", `잡 '${job.name}' ${next ? "활성화" : "일시 정지"} 완료.`);
    } catch (e) {
      toast.show("error", describe(e));
    } finally {
      setBusyJob(null);
    }
  };

  const confirmRunNow = async () => {
    if (!pendingRunNow) return;
    const job = pendingRunNow;
    setBusyJob(job.name);
    try {
      const result = await runCronJobNow(job.name);
      toast.show(result.ok ? "success" : "error", result.message);
      await refresh();
      // 사용자가 이미 같은 잡의 Drawer를 열어 두었다면 새 실행을 즉시 노출.
      if (selected && selected.name === job.name) {
        setRunsRefresh((n) => n + 1);
      }
    } catch (e) {
      toast.show("error", describe(e));
    } finally {
      setBusyJob(null);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const job = pendingDelete;
    setBusyJob(job.name);
    try {
      await deleteCronJob(job.name);
      // Drawer가 같은 잡을 가리켰다면 닫는다.
      if (selected && selected.name === job.name) setSelected(null);
      await refresh();
      toast.show("success", `잡 '${job.name}' 삭제 완료.`);
    } catch (e) {
      toast.show("error", describe(e));
    } finally {
      setBusyJob(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold text-(--foreground-strong)">Cron</h1>
          <p className="text-sm text-(--muted-foreground)">
            잡 목록·표현식·실행 이력을 한 화면에서 운영하세요.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<RefreshCw size={14} aria-hidden />}
            onClick={() => refresh()}
            aria-label="새로고침"
          >
            새로고침
          </Button>
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus size={14} aria-hidden />}
            onClick={() => setShowNew(true)}
          >
            새 잡
          </Button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="이름으로 검색"
          containerClassName="w-64"
        />
        <div className="flex items-center gap-1 rounded-(--radius-pill) border border-(--border) bg-(--card) p-1">
          {(
            [
              ["all", "전체"],
              ["enabled", "활성"],
              ["paused", "일시정지"],
              ["circuit_open", "circuit-break"],
            ] as Array<[StatusFilter, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setStatusFilter(key)}
              aria-pressed={statusFilter === key}
              className={
                "rounded-(--radius-pill) px-3 py-1 text-xs font-medium transition-colors " +
                (statusFilter === key
                  ? "bg-(--primary) text-(--primary-foreground)"
                  : "text-(--muted-foreground) hover:text-(--foreground)")
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {jobs === null ? (
        <p className="text-sm text-(--muted-foreground)">불러오는 중…</p>
      ) : filtered.length === 0 ? (
        <EmptyState
          hasJobs={jobs.length > 0}
          onCreate={() => setShowNew(true)}
        />
      ) : (
        <CronJobsTable
          jobs={filtered}
          busyJobName={busyJob}
          onSelect={setSelected}
          onToggleEnabled={onToggleEnabled}
          onRunNow={(job) => setPendingRunNow(job)}
          onDelete={(job) => setPendingDelete(job)}
        />
      )}

      <NewJobModal
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={() => {
          // 모달이 자체 입력 초기화 후 닫고, 부모는 목록 갱신만.
          refresh().catch(() => {});
          toast.show("success", "새 잡이 추가됐어요.");
        }}
      />

      <RunHistoryDrawer
        job={selected}
        refreshKey={runsRefresh}
        onClose={() => setSelected(null)}
      />

      <ConfirmGate
        open={!!pendingRunNow}
        onClose={() => setPendingRunNow(null)}
        onConfirm={confirmRunNow}
        title={pendingRunNow ? `'${pendingRunNow.name}' 즉시 실행` : ""}
        description="대화 히스토리와 격리된 컨텍스트에서 잡이 실행돼요."
        confirmLabel="실행"
        tone="primary"
      />

      <ConfirmGate
        open={!!pendingDelete}
        onClose={() => setPendingDelete(null)}
        onConfirm={confirmDelete}
        title={pendingDelete ? `'${pendingDelete.name}' 삭제` : ""}
        description="삭제 후에는 되돌릴 수 없어요. 실행 이력도 함께 사라져요."
        confirmLabel="삭제"
        requireText={pendingDelete?.name}
      />
    </div>
  );
}

function EmptyState({
  hasJobs,
  onCreate,
}: {
  hasJobs: boolean;
  onCreate: () => void;
}) {
  // hasJobs=true이면 필터 결과가 없는 것 — "조건을 줄여 보세요" 안내.
  return (
    <section className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-(--radius-l) border border-dashed border-(--border-strong) bg-(--card) px-8 py-12 text-center">
      <h2 className="text-lg font-semibold text-(--foreground-strong)">
        {hasJobs ? "조건에 맞는 잡이 없어요" : "아직 등록된 잡이 없어요"}
      </h2>
      <p className="text-sm text-(--muted-foreground)">
        {hasJobs
          ? "검색어나 상태 필터를 풀어 보세요."
          : "정기적으로 실행할 작업을 새 잡으로 등록해 주세요."}
      </p>
      {!hasJobs ? (
        <Button variant="primary" size="sm" onClick={onCreate}>
          첫 잡 만들기
        </Button>
      ) : null}
    </section>
  );
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
