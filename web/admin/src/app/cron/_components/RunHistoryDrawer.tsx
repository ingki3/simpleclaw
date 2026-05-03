"use client";

/**
 * 잡 상세 — 최근 실행 20건 + 한 항목 펼치면 stdout/stderr 스냅샷.
 *
 * 데이터는 ``listCronRuns(jobName)`` 으로 매 open마다 재조회한다. 내장
 * 캐시는 두지 않는다 — Run-now 직후의 새 실행을 즉시 반영하기 위함.
 */

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Drawer } from "../_primitives/Drawer";
import { listCronRuns } from "@/lib/cron/client";
import type { CronJob, CronRun } from "@/lib/cron/types";

const TONE: Record<string, StatusTone> = {
  success: "success",
  failed: "error",
  running: "info",
  skipped: "neutral",
};

const LABEL: Record<string, string> = {
  success: "성공",
  failed: "실패",
  running: "실행 중",
  skipped: "건너뜀",
};

export interface RunHistoryDrawerProps {
  job: CronJob | null;
  /** 외부에서 ``Run now`` 등으로 새 실행을 만든 경우 carry해 강제 재조회. */
  refreshKey: number;
  onClose: () => void;
}

export function RunHistoryDrawer({
  job,
  refreshKey,
  onClose,
}: RunHistoryDrawerProps) {
  const [runs, setRuns] = useState<CronRun[] | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    if (!job) {
      setRuns(null);
      setExpanded(null);
      return;
    }
    let cancelled = false;
    setRuns(null);
    listCronRuns(job.name, 20).then((data) => {
      if (!cancelled) setRuns(data);
    });
    return () => {
      cancelled = true;
    };
  }, [job, refreshKey]);

  return (
    <Drawer
      open={!!job}
      onClose={onClose}
      title={job ? `${job.name} 실행 이력` : ""}
      description={
        job
          ? `${job.cronExpression} · ${job.actionType}: ${truncate(job.actionReference, 64)}`
          : undefined
      }
    >
      {!job ? null : runs === null ? (
        <p className="text-sm text-[--muted-foreground]">불러오는 중…</p>
      ) : runs.length === 0 ? (
        <p className="text-sm text-[--muted-foreground]">
          아직 실행된 적이 없어요. ``Run now`` 로 한 번 실행해 결과를 확인해 보세요.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {runs.map((run) => {
            const open = expanded === run.id;
            return (
              <li
                key={run.id}
                className="rounded-[--radius-m] border border-[--border]"
              >
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : run.id)}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-[--surface]"
                  aria-expanded={open}
                >
                  {open ? (
                    <ChevronDown size={14} aria-hidden />
                  ) : (
                    <ChevronRight size={14} aria-hidden />
                  )}
                  <StatusPill tone={TONE[run.status] ?? "neutral"}>
                    {LABEL[run.status] ?? run.status}
                  </StatusPill>
                  <span className="font-mono text-xs text-[--muted-foreground]">
                    {formatTimestamp(run.startedAt)}
                  </span>
                  {run.attempt > 1 ? (
                    <span className="text-xs text-[--muted-foreground]">
                      재시도 #{run.attempt}
                    </span>
                  ) : null}
                </button>
                {open ? (
                  <div className="border-t border-[--border-divider] bg-[--surface] px-3 py-3 text-xs">
                    {run.resultSummary ? (
                      <Section title="결과">
                        <Pre>{run.resultSummary}</Pre>
                      </Section>
                    ) : null}
                    {run.errorDetails ? (
                      <Section title="에러">
                        <Pre tone="error">{run.errorDetails}</Pre>
                      </Section>
                    ) : null}
                    {!run.resultSummary && !run.errorDetails ? (
                      <p className="text-[--muted-foreground]">스냅샷이 비어 있어요.</p>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Drawer>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-2 last:mb-0">
      <div className="mb-1 text-[--muted-foreground]">{title}</div>
      {children}
    </div>
  );
}

function Pre({
  children,
  tone = "default",
}: {
  children: React.ReactNode;
  tone?: "default" | "error";
}) {
  return (
    <pre
      className={
        "overflow-x-auto whitespace-pre-wrap break-words rounded-[--radius-sm] bg-[--card] px-2 py-2 font-mono text-[--foreground]" +
        (tone === "error" ? " text-[--color-error]" : "")
      }
    >
      {children}
    </pre>
  );
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
