/**
 * ActiveProjectsPanel — admin.pen `XFipm` BIZ-66 패널을 Dashboard 로 mirror.
 *
 * Memory 영역 상단에 두었던 패널을 Dashboard 의 메인 영역에도 동일 형태로 노출한다.
 * 본 컴포넌트는 `state` 4-variant (default / empty / loading / error) 모두를 지원한다 —
 * DESIGN.md §1 Principle 3 / §4.6 (모든 영역의 Empty/Loading/Error variant 박제).
 *
 * 본 단계는 Dashboard 전용 _components 에 두지만, S6 (Memory) 가 동일 패널을 사용할 때
 * `@/design/domain` 으로 승격할 수 있도록 prop 시그니처는 도메인-중립으로 유지.
 */
"use client";

import Link from "next/link";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { ActiveProject } from "../_data";

export type ActiveProjectsState = "default" | "empty" | "loading" | "error";

export interface ActiveProjectsPanelProps {
  state: ActiveProjectsState;
  projects?: readonly ActiveProject[];
  /** state="error" 일 때 노출할 사람 친화 사유. */
  errorMessage?: string;
  /** error 또는 empty 에서 운영자가 누를 액션 — error: 재시도 / empty: 새로 시작. */
  onRetry?: () => void;
  className?: string;
}

const SKELETON_ROWS = 3;

export function ActiveProjectsPanel({
  state,
  projects = [],
  errorMessage = "Active Projects 를 불러오지 못했습니다.",
  onRetry,
  className,
}: ActiveProjectsPanelProps) {
  return (
    <section
      data-testid="active-projects-panel"
      data-state={state}
      aria-label="Active Projects"
      aria-busy={state === "loading" || undefined}
      className={cn(
        "flex flex-col gap-4 rounded-(--radius-l) border border-(--border) bg-(--card) p-6 shadow-(--shadow-sm)",
        className,
      )}
    >
      <header className="flex items-baseline justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-semibold text-(--foreground-strong)">
            Active Projects
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            메모리 클러스터에서 자동 추출된 진행 중 프로젝트 (BIZ-66).
          </p>
        </div>
        <Link
          href="/memory"
          data-testid="active-projects-source"
          className="text-xs font-medium text-(--primary) hover:underline"
        >
          기억 영역에서 보기 →
        </Link>
      </header>

      {state === "loading" ? <PanelLoading /> : null}
      {state === "empty" ? <PanelEmpty onAction={onRetry} /> : null}
      {state === "error" ? (
        <PanelError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "default" ? <PanelList projects={projects} /> : null}
    </section>
  );
}

function PanelList({ projects }: { projects: readonly ActiveProject[] }) {
  if (projects.length === 0) {
    // default 인데 데이터가 0개 — 호출자 실수 방지로 EmptyState 폴백.
    return <PanelEmpty />;
  }
  return (
    <ul
      data-testid="active-projects-list"
      className="flex flex-col divide-y divide-(--border)"
    >
      {projects.map((p) => (
        <li
          key={p.id}
          data-testid={`active-project-${p.id}`}
          className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0"
        >
          <div className="flex items-center gap-2">
            <code className="rounded-(--radius-sm) bg-(--surface) px-1.5 py-0.5 font-mono text-[11px] text-(--foreground)">
              {p.identifier}
            </code>
            <StatusPill tone={p.statusTone}>{p.statusLabel}</StatusPill>
            <span className="ml-auto text-xs text-(--muted-foreground)">
              {p.owner} · {p.updatedAt}
            </span>
          </div>
          <p className="text-sm font-medium text-(--foreground-strong)">
            {p.title}
          </p>
          <p className="line-clamp-2 text-xs text-(--muted-foreground)">
            {p.excerpt}
          </p>
        </li>
      ))}
    </ul>
  );
}

function PanelLoading() {
  return (
    <ul
      data-testid="active-projects-loading"
      role="status"
      aria-label="Active Projects 로딩 중"
      className="flex flex-col divide-y divide-(--border)"
    >
      {Array.from({ length: SKELETON_ROWS }).map((_, i) => (
        <li
          key={i}
          className="flex animate-pulse flex-col gap-2 py-3 first:pt-0 last:pb-0"
        >
          <div className="flex items-center gap-2">
            <span className="h-4 w-12 rounded-(--radius-sm) bg-(--surface)" />
            <span className="h-4 w-16 rounded-(--radius-sm) bg-(--surface)" />
            <span className="ml-auto h-3 w-24 rounded-(--radius-sm) bg-(--surface)" />
          </div>
          <span className="h-4 w-3/5 rounded-(--radius-sm) bg-(--surface)" />
          <span className="h-3 w-4/5 rounded-(--radius-sm) bg-(--surface)" />
        </li>
      ))}
    </ul>
  );
}

function PanelEmpty({ onAction }: { onAction?: () => void } = {}) {
  return (
    <div data-testid="active-projects-empty">
      <EmptyState
        title="Active Projects 가 비어 있습니다"
        description="대화·드리밍이 충분히 누적되면 자동으로 채워집니다. 수동으로 추가하려면 기억 영역에서 시작하세요."
        action={
          onAction ? (
            <Button size="sm" variant="secondary" onClick={onAction}>
              기억 영역으로 이동
            </Button>
          ) : (
            <Link
              href="/memory"
              className="text-sm font-medium text-(--primary) hover:underline"
            >
              기억 영역으로 이동 →
            </Link>
          )
        }
      />
    </div>
  );
}

function PanelError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="active-projects-error"
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-4 text-sm"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone="error">실패</StatusPill>
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
          data-testid="active-projects-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
