"use client";

/**
 * DryRunDiff — LLM Router 페이지(BIZ-45) 전용 dry-run 결과 패널.
 *
 * admin_api.PATCH ``?dry_run=true`` 응답의 ``diff.{before, after}``를 키 단위로
 * 비교해 ``+ / − / ≠`` 라인을 생성한다. 본 1차 구현은 단순 평탄화 후 한 줄씩
 * 노출 — 더 깊은 비교(중첩 객체 다이프, 색조 강조)는 BIZ-43의 정식 ``DiffView``가
 * 들어오면 그쪽으로 위임한다.
 *
 * 정책 라벨(Hot / Service-restart / Process-restart)은 dry-run 응답의 ``policy``
 * 필드를 그대로 ``PolicyPill``로 표시한다.
 */

import { useMemo } from "react";
import type { DryRunResponse } from "@/lib/api/llm";
import { PolicyPill, type PolicyLevel } from "@/components/atoms/PolicyPill";
import { Badge } from "@/components/atoms/Badge";
import { cn } from "@/lib/cn";

export interface DryRunDiffProps {
  result: DryRunResponse | null;
  /** 현재 dry-run을 실행 중인지 — 진행 표시. */
  loading?: boolean;
  className?: string;
}

interface DiffLine {
  /** dotted path. */
  key: string;
  /** ``+`` 추가 / ``-`` 제거 / ``~`` 변경. */
  op: "+" | "-" | "~";
  before: unknown;
  after: unknown;
}

function flatten(prefix: string, value: unknown, out: Record<string, unknown>): void {
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  ) {
    const obj = value as Record<string, unknown>;
    if (Object.keys(obj).length === 0) {
      out[prefix] = obj;
      return;
    }
    for (const [k, v] of Object.entries(obj)) {
      flatten(prefix ? `${prefix}.${k}` : k, v, out);
    }
    return;
  }
  out[prefix] = value;
}

function diff(before: unknown, after: unknown): DiffLine[] {
  const a: Record<string, unknown> = {};
  const b: Record<string, unknown> = {};
  flatten("", before ?? {}, a);
  flatten("", after ?? {}, b);
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  const lines: DiffLine[] = [];
  for (const k of Array.from(keys).sort()) {
    const av = a[k];
    const bv = b[k];
    if (!(k in a)) {
      lines.push({ key: k, op: "+", before: undefined, after: bv });
    } else if (!(k in b)) {
      lines.push({ key: k, op: "-", before: av, after: undefined });
    } else if (JSON.stringify(av) !== JSON.stringify(bv)) {
      lines.push({ key: k, op: "~", before: av, after: bv });
    }
  }
  return lines;
}

function fmt(v: unknown): string {
  if (v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

const POLICY_TO_LEVEL: Record<string, PolicyLevel> = {
  Hot: "hot",
  "Service-restart": "service-restart",
  "Process-restart": "process-restart",
};

export function DryRunDiff({ result, loading, className }: DryRunDiffProps) {
  const lines = useMemo(() => {
    if (!result) return [];
    return diff(result.diff.before, result.diff.after);
  }, [result]);

  if (loading) {
    return (
      <div
        className={cn(
          "rounded-[--radius-m] border border-dashed border-[--border-divider] bg-[--surface] p-4 text-sm text-[--muted-foreground]",
          className,
        )}
      >
        Dry-run 실행 중…
      </div>
    );
  }

  if (!result) {
    return (
      <div
        className={cn(
          "rounded-[--radius-m] border border-dashed border-[--border-divider] bg-[--surface] p-4 text-sm text-[--muted-foreground]",
          className,
        )}
      >
        변경사항을 입력한 뒤 <strong>Dry-run</strong>으로 영향과 정책을 미리 확인하세요.
        실제 적용은 dry-run 통과 후 활성화됩니다.
      </div>
    );
  }

  const policyLevel = POLICY_TO_LEVEL[result.policy.level] ?? "hot";

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-[--radius-m] border border-[--border] bg-[--card] p-4",
        className,
      )}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-[--foreground-strong]">
          Dry-run 결과
        </span>
        <PolicyPill level={policyLevel} />
        {result.policy.affected_modules.map((m) => (
          <Badge key={m} tone="info">
            {m}
          </Badge>
        ))}
        {lines.length === 0 ? (
          <Badge tone="neutral" className="ml-auto">
            변경 없음
          </Badge>
        ) : (
          <Badge tone="brand" className="ml-auto">
            {lines.length}건
          </Badge>
        )}
      </header>
      {lines.length > 0 ? (
        <ul className="flex flex-col gap-1 font-mono text-xs">
          {lines.map((l) => (
            <li
              key={`${l.op}-${l.key}`}
              className="flex flex-wrap items-baseline gap-2 rounded-[--radius-sm] bg-[--surface] px-2 py-1"
            >
              <span
                aria-hidden
                className={cn(
                  "inline-flex h-4 w-4 items-center justify-center rounded-[--radius-sm] text-[10px] font-bold",
                  l.op === "+"
                    ? "bg-[--color-success-bg] text-[--color-success]"
                    : l.op === "-"
                    ? "bg-[--color-error-bg] text-[--color-error]"
                    : "bg-[--color-warning-bg] text-[--color-warning]",
                )}
              >
                {l.op}
              </span>
              <span className="text-[--muted-foreground]">{l.key}</span>
              {l.op !== "+" ? (
                <span className="text-[--color-error]">{fmt(l.before)}</span>
              ) : null}
              {l.op !== "-" ? (
                <>
                  <span className="text-[--muted-foreground]" aria-hidden>
                    →
                  </span>
                  <span className="text-[--color-success]">{fmt(l.after)}</span>
                </>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
