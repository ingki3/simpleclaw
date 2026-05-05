"use client";

/**
 * TraceDetailModal — admin.pen `EvyYa` (Trace Detail) 시각 spec.
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — trace 이름 + 상태 StatusPill + 트리거 시각.
 *  2) 요약 카드 — meta 행 (service, duration, status 등).
 *  3) TraceTimeline — span lane (Domain reusable).
 *  4) Span 인스펙터 — 행 클릭 시 상세 (시작/종료/지속).
 *  5) Raw JSON — code 블록.
 *
 * 본 단계는 정적 fixture — span 클릭 → 인스펙터 갱신 외 mutation 없음.
 */

import { useEffect, useState } from "react";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill, type StatusTone } from "@/design/atoms/StatusPill";
import { TraceTimeline, type TraceSpan } from "@/design/domain/TraceTimeline";
import { cn } from "@/lib/cn";
import { Modal } from "./Modal";
import type { TraceDetail, TraceMeta } from "../_data";

interface TraceDetailModalProps {
  open: boolean;
  trace: TraceDetail | null;
  onClose: () => void;
}

const STATUS_TONE: Record<TraceDetail["status"], StatusTone> = {
  success: "success",
  failed: "error",
  running: "info",
};

const STATUS_LABEL: Record<TraceDetail["status"], string> = {
  success: "성공",
  failed: "실패",
  running: "실행 중",
};

const META_TONE: Record<NonNullable<TraceMeta["tone"]>, string> = {
  neutral: "text-(--muted-foreground)",
  success: "text-(--color-success)",
  warning: "text-(--color-warning)",
  error: "text-(--color-error)",
  info: "text-(--color-info)",
};

export function TraceDetailModal({
  open,
  trace,
  onClose,
}: TraceDetailModalProps) {
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);

  // trace 가 바뀌면 인스펙터 선택을 초기화 — 첫 span 을 기본 선택.
  useEffect(() => {
    if (!trace) {
      setSelectedSpanId(null);
      return;
    }
    setSelectedSpanId(trace.spans[0]?.id ?? null);
  }, [trace]);

  if (!trace) return null;

  const selected =
    trace.spans.find((s) => s.id === selectedSpanId) ?? trace.spans[0] ?? null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="xl"
      data-testid="trace-detail-modal"
      title={
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            {trace.name}
          </h2>
          <StatusPill tone={STATUS_TONE[trace.status]}>
            {STATUS_LABEL[trace.status]}
          </StatusPill>
          <span
            data-testid="trace-detail-id"
            className="font-mono text-xs text-(--muted-foreground)"
          >
            {trace.id}
          </span>
          <span
            className="text-xs text-(--muted-foreground)"
            data-testid="trace-detail-started-at"
          >
            {formatDateTime(trace.startedAt)}
          </span>
        </div>
      }
      footer={
        <Button
          size="sm"
          variant="secondary"
          onClick={onClose}
          data-testid="trace-detail-close"
        >
          닫기
        </Button>
      }
    >
      <section
        data-testid="trace-detail-summary"
        className="grid grid-cols-2 gap-3 rounded-(--radius-l) border border-(--border) bg-(--surface) p-4 sm:grid-cols-3"
      >
        {trace.meta.map((m) => (
          <div key={m.label} className="flex flex-col gap-0.5">
            <span className="text-[11px] uppercase tracking-wide text-(--muted-foreground)">
              {m.label}
            </span>
            <span
              className={cn(
                "font-mono text-sm",
                m.tone ? META_TONE[m.tone] : "text-(--foreground)",
              )}
            >
              {m.value}
            </span>
          </div>
        ))}
      </section>

      <TraceTimeline
        spans={trace.spans as TraceSpan[]}
        totalMs={trace.totalMs}
      />

      <section
        data-testid="trace-detail-span-inspector"
        className="rounded-(--radius-l) border border-(--border) bg-(--card) p-4"
      >
        <header className="mb-2 flex items-center justify-between text-xs text-(--muted-foreground)">
          <span>Span 인스펙터</span>
          <span>span 행을 클릭하면 상세를 볼 수 있어요.</span>
        </header>
        <ul className="flex flex-col divide-y divide-(--border)">
          {trace.spans.map((span) => {
            const isSelected = selected?.id === span.id;
            return (
              <li key={span.id}>
                <button
                  type="button"
                  data-testid={`trace-detail-span-${span.id}`}
                  data-selected={isSelected || undefined}
                  onClick={() => setSelectedSpanId(span.id)}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 px-2 py-2 text-left text-xs transition-colors",
                    isSelected
                      ? "bg-(--surface) text-(--foreground-strong)"
                      : "text-(--foreground) hover:bg-(--surface)",
                  )}
                >
                  <span className="flex items-center gap-2">
                    <Badge tone="neutral" size="sm">
                      {span.tone ?? "primary"}
                    </Badge>
                    <span className="font-mono">{span.name}</span>
                  </span>
                  <span className="font-mono tabular-nums text-(--muted-foreground)">
                    {span.startMs}–{span.endMs} ms · {span.endMs - span.startMs}{" "}
                    ms
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section
        data-testid="trace-detail-raw"
        className="flex flex-col gap-2 rounded-(--radius-l) border border-(--border) bg-(--card) p-4"
      >
        <header className="flex items-center justify-between text-xs text-(--muted-foreground)">
          <span>Raw JSON</span>
          <Badge tone="neutral" size="sm">
            otel
          </Badge>
        </header>
        <pre className="overflow-x-auto rounded-(--radius-m) bg-(--surface) p-3 font-mono text-xs leading-relaxed text-(--foreground)">
          {JSON.stringify(trace.rawJson, null, 2)}
        </pre>
      </section>
    </Modal>
  );
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const yyyy = d.getUTCFullYear();
  const MM = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${yyyy}-${MM}-${dd} ${hh}:${mm} UTC`;
}
