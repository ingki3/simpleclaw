/**
 * CronJobRow — Domain. 크론 잡 한 줄 표시 (DESIGN.md §3.4).
 *
 * 컬럼: 이름 / 스케줄 / 다음 실행 / 상태 / circuit / 액션.
 * Compact 테이블 row 스타일 — padding [8, 12].
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Code } from "../atoms/Code";
import { StatusPill, type StatusTone } from "../atoms/StatusPill";
import { HealthDot, type HealthTone } from "../molecules/HealthDot";

export type CircuitState = "closed" | "half-open" | "open";

export interface CronJobRowProps {
  name: string;
  /** crontab 형식 또는 사람 친화 표현. */
  schedule: string;
  /** 다음 실행 시각 — ISO 또는 사람 친화 문자열. */
  nextRun?: string;
  status: "idle" | "running" | "success" | "failed";
  circuit?: CircuitState;
  /** 우측 끝 액션 슬롯 (예: 즉시 실행, 비활성화 토글). */
  actions?: ReactNode;
  className?: string;
}

const STATUS_TONE: Record<CronJobRowProps["status"], StatusTone> = {
  idle: "neutral",
  running: "info",
  success: "success",
  failed: "error",
};

const STATUS_LABEL: Record<CronJobRowProps["status"], string> = {
  idle: "대기",
  running: "실행중",
  success: "성공",
  failed: "실패",
};

const CIRCUIT_TONE: Record<CircuitState, HealthTone> = {
  closed: "green",
  "half-open": "amber",
  open: "red",
};

const CIRCUIT_LABEL: Record<CircuitState, string> = {
  closed: "정상",
  "half-open": "복구중",
  open: "차단",
};

export function CronJobRow({
  name,
  schedule,
  nextRun,
  status,
  circuit,
  actions,
  className,
}: CronJobRowProps) {
  return (
    <tr
      className={cn(
        "border-b border-(--border) text-sm hover:bg-(--surface)",
        className,
      )}
    >
      <td className="px-3 py-2 font-medium text-(--foreground)">{name}</td>
      <td className="px-3 py-2">
        <Code>{schedule}</Code>
      </td>
      <td className="px-3 py-2 text-(--muted-foreground)">
        {nextRun ?? "—"}
      </td>
      <td className="px-3 py-2">
        <StatusPill tone={STATUS_TONE[status]}>
          {STATUS_LABEL[status]}
        </StatusPill>
      </td>
      <td className="px-3 py-2">
        {circuit ? (
          <HealthDot
            tone={CIRCUIT_TONE[circuit]}
            label={CIRCUIT_LABEL[circuit]}
          />
        ) : null}
      </td>
      <td className="px-3 py-2 text-right">{actions}</td>
    </tr>
  );
}
