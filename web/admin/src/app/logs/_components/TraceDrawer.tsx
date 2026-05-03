"use client";

/**
 * TraceDrawer — 단일 trace_id로 묶인 로그 항목 + 원문 JSON을 우측에서 보여주는 패널.
 *
 * 동작:
 *  - ``trace_id``가 있으면 ``/admin/v1/logs?trace_id=...&limit=500``으로 묶음을 가져온다.
 *  - 비어있으면 페이지에서 클릭한 단일 항목만 펼쳐 보여준다(과거 trace_id 미부여 로그 대응).
 *  - 본문은 두 영역으로 분할: (1) 타임라인(시각/모듈/요약), (2) 클릭한 단일 항목 JSON.
 *
 * 의도적으로 가져오기를 ``useAdminResource``에 위임 — 폴링 없이 단발 fetch.
 * Drawer가 닫힐 때 path가 바뀌어 자동으로 cleanup된다.
 */

import { useMemo } from "react";
import { Drawer } from "@/components/primitives/Drawer";
import { Badge, type BadgeTone } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { useAdminResource } from "@/lib/api/use-admin-resource";
import {
  buildLogsPath,
  normalizeLevel,
  type LogApiEntry,
  type LogLevel,
  type LogsResponse,
} from "@/lib/api/logs";

const LEVEL_TONE: Record<LogLevel, BadgeTone> = {
  DEBUG: "neutral",
  INFO: "info",
  WARNING: "warning",
  ERROR: "danger",
};

export interface TraceDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 페이지에서 클릭한 항목 — 항상 fallback으로 사용. */
  selected: LogApiEntry | null;
}

export function TraceDrawer({ open, onOpenChange, selected }: TraceDrawerProps) {
  const traceId = selected?.trace_id || "";
  // trace_id가 없으면 fetch를 건너뛴다(enabled=false).
  const trace = useAdminResource<LogsResponse>(
    traceId
      ? buildLogsPath({ limit: 500, traceId })
      : "/admin/v1/logs",
    { enabled: open && Boolean(traceId) },
  );

  const grouped = useMemo<LogApiEntry[]>(() => {
    if (!traceId) return selected ? [selected] : [];
    if (!trace.data?.entries?.length) return selected ? [selected] : [];
    // 백엔드는 시간순 오름차순 — 타임라인 가독성 그대로 유지.
    return trace.data.entries;
  }, [traceId, trace.data, selected]);

  const detailJson = useMemo(() => {
    if (!selected) return "";
    try {
      return JSON.stringify(selected, null, 2);
    } catch {
      return String(selected);
    }
  }, [selected]);

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={
        traceId ? (
          <span className="font-mono text-sm">trace {traceId.slice(0, 12)}…</span>
        ) : (
          "로그 상세"
        )
      }
      description={
        traceId
          ? `같은 trace_id로 묶인 ${grouped.length}개 항목`
          : "trace_id가 없는 단발 항목입니다."
      }
      size="lg"
    >
      <section className="flex flex-col gap-4">
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[--muted-foreground]">
            타임라인
          </h3>
          {trace.isLoading && !trace.data ? (
            <p className="text-sm text-[--muted-foreground]">로그를 불러오는 중…</p>
          ) : trace.error && traceId ? (
            <p className="text-sm text-[--color-error]">
              trace 묶음을 불러오지 못했습니다 — 클릭한 항목만 표시합니다.
            </p>
          ) : null}
          <ol className="flex flex-col gap-2">
            {grouped.map((e, i) => {
              const level = normalizeLevel(e.level) ?? "INFO";
              const isSelected =
                selected &&
                e.timestamp === selected.timestamp &&
                e.action_type === selected.action_type;
              return (
                <li
                  key={`${e.timestamp ?? "ts"}-${i}`}
                  className={
                    "rounded-[--radius-sm] border px-3 py-2 " +
                    (isSelected
                      ? "border-[--primary] bg-[--primary-tint]"
                      : "border-[--border] bg-[--card]")
                  }
                >
                  <div className="flex items-center gap-2 text-xs text-[--muted-foreground]">
                    <Badge tone={LEVEL_TONE[level]}>{level.toLowerCase()}</Badge>
                    <span className="font-mono">
                      {e.timestamp ? new Date(e.timestamp).toLocaleTimeString("ko-KR") : "—"}
                    </span>
                    {typeof e.duration_ms === "number" ? (
                      <span>· {e.duration_ms.toFixed(0)} ms</span>
                    ) : null}
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-[--foreground-strong]">
                    {e.action_type || "(no action_type)"}
                  </p>
                  {e.input_summary ? (
                    <p className="mt-1 line-clamp-2 text-xs text-[--foreground]">
                      {e.input_summary}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[--muted-foreground]">
              JSON 원문 (선택 항목)
            </h3>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                if (typeof navigator !== "undefined" && navigator.clipboard) {
                  void navigator.clipboard.writeText(detailJson);
                }
              }}
            >
              복사
            </Button>
          </div>
          <pre className="max-h-[420px] overflow-auto rounded-[--radius-sm] border border-[--border-divider] bg-[--surface] p-3 font-mono text-xs leading-relaxed text-[--foreground]">
            <code>{detailJson || "{}"}</code>
          </pre>
        </div>
      </section>
    </Drawer>
  );
}
