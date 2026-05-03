/**
 * StatusCard — 대시보드의 상태 카드 1종(라벨 + 큰 값 + 보조 텍스트 + 상태 pill).
 *
 * DESIGN.md §3.2 MetricCard에 대응. 로딩/에러/빈 상태(`value === null`)를 한 컴포넌트가 일관되게 표현한다.
 * - 헤더는 `<h3>` — 페이지 타이틀(h1)과 영역 카드(h2) 아래 의미 계층을 유지.
 * - 색만으로 상태를 전달하지 않도록 `StatusPill`(dot + 라벨)을 동반한다.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";

export interface StatusCardProps {
  title: string;
  /** 표시할 큰 값. `null`이면 빈 상태(`—`)로 렌더. */
  value: string | number | null;
  /** 값 아래 한 줄 보조 — 단위, 직전 값, 짧은 부연 등. */
  hint?: ReactNode;
  /** 우상단 상태 pill — 데몬 running/idle 등. */
  status?: { tone: StatusTone; label: string };
  /** 로딩 상태 — true면 값 자리는 스켈레톤. */
  isLoading?: boolean;
  /** 에러 메시지 — 있으면 값/hint 대신 표시. */
  error?: string;
  className?: string;
}

export function StatusCard({
  title,
  value,
  hint,
  status,
  isLoading,
  error,
  className,
}: StatusCardProps) {
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-6",
        className,
      )}
      aria-busy={isLoading || undefined}
    >
      <header className="flex items-start justify-between gap-2">
        <h2 className="text-sm font-medium text-(--muted-foreground)">
          {title}
        </h2>
        {status ? (
          <StatusPill tone={status.tone}>{status.label}</StatusPill>
        ) : null}
      </header>
      {error ? (
        <p className="text-sm text-(--color-error)" role="alert">
          {error}
        </p>
      ) : isLoading && value === null ? (
        <div
          aria-hidden
          className="h-8 w-24 animate-pulse rounded-(--radius-sm) bg-(--surface)"
        />
      ) : (
        <p className="text-3xl font-semibold leading-none text-(--foreground-strong)">
          {value === null || value === undefined ? "—" : value}
        </p>
      )}
      {hint && !error ? (
        <p className="text-xs text-(--muted-foreground)">{hint}</p>
      ) : null}
    </section>
  );
}
