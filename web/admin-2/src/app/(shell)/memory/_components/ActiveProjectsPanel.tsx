/**
 * ActiveProjectsPanel — admin.pen `XFipm` (BIZ-66 Active Projects 패널) 박제.
 *
 * `/memory` 화면 최상단에 위치하며, dreaming 사이클이 산출하는 active projects
 * 와 managed marker 정책을 한 시야에서 보여준다.
 *
 * DESIGN.md §1 Principle 3 — default / loading / empty / error 4-variant 를
 * 반드시 노출. variant 검증은 `?projects=loading|empty|error` 쿼리로 page.tsx 에서
 * 강제한다 (cron / skills-recipes 동일 패턴).
 *
 * mutation 책임은 없다 — sidecar 갱신은 dreaming 사이클에서만 발생하므로 본
 * 패널은 표상만 책임. "왜 USER.md 의 프로젝트 섹션이 비어 있나" 를 운영자가
 * 한 시야에서 진단할 수 있게 하는 것이 목적.
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { cn } from "@/lib/cn";
import type { ActiveProject } from "../_data";

export type ActiveProjectsState = "default" | "empty" | "loading" | "error";

interface ActiveProjectsPanelProps {
  state: ActiveProjectsState;
  projects?: readonly ActiveProject[];
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  /**
   * managed 토글 — 실제 mutation 은 dreaming 가 담당하지만, S8 박제 단계에서는
   * fixture 카피를 갱신해 시각적으로 확인.
   */
  onToggleManaged?: (id: string, next: boolean) => void;
  className?: string;
}

const SKELETON_COUNT = 3;

export function ActiveProjectsPanel({
  state,
  projects = [],
  errorMessage = "Active Projects 를 불러오지 못했습니다.",
  onRetry,
  onToggleManaged,
  className,
}: ActiveProjectsPanelProps) {
  const total = projects.length;
  const managedCount = projects.filter((p) => p.managed).length;

  return (
    <section
      data-testid="active-projects-panel"
      data-state={state}
      aria-label="Active Projects"
      aria-busy={state === "loading" || undefined}
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5",
        className,
      )}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-semibold text-(--foreground-strong)">
            Active Projects · 자동 관리
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            managed marker 보존 · 단발 관측 자동 승격 차단 (BIZ-66 Gate).
          </p>
        </div>
        {state === "default" ? (
          <div
            data-testid="active-projects-counts"
            className="flex items-center gap-2 text-xs text-(--muted-foreground)"
          >
            <Badge tone="brand">전체 {total}</Badge>
            <Badge tone={managedCount > 0 ? "info" : "neutral"}>
              managed {managedCount}
            </Badge>
          </div>
        ) : null}
      </header>

      {state === "loading" ? <PanelLoading /> : null}
      {state === "error" ? (
        <PanelError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <PanelEmpty /> : null}
      {state === "default" ? (
        projects.length === 0 ? (
          <PanelEmpty />
        ) : (
          <ul
            data-testid="active-projects-list"
            className="flex flex-col gap-2"
          >
            {projects.map((p) => (
              <ActiveProjectRow
                key={p.id}
                project={p}
                onToggleManaged={onToggleManaged}
              />
            ))}
          </ul>
        )
      ) : null}

      <footer className="text-[11px] leading-relaxed text-(--muted-foreground)">
        managed marker 가 있는 항목은 dreaming decay 에서 제외됩니다. 단발 관측은
        cluster 채택 임계 (0.60) 미만이면 후보 큐로 보내고 자동 승격하지 않습니다.
      </footer>
    </section>
  );
}

function ActiveProjectRow({
  project,
  onToggleManaged,
}: {
  project: ActiveProject;
  onToggleManaged?: (id: string, next: boolean) => void;
}) {
  const updated = formatRelative(project.updatedAt);
  return (
    <li
      data-testid={`active-project-${project.id}`}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2"
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-2 w-2 rounded-(--radius-pill)",
          project.managed ? "bg-(--color-info)" : "bg-(--muted-foreground)",
        )}
      />
      <span className="flex-1 text-sm font-medium text-(--foreground-strong)">
        {project.title}
      </span>
      <span className="tabular-nums text-xs text-(--muted-foreground)">
        score {project.score.toFixed(2)} · {updated}
      </span>
      {project.managed ? (
        <Badge tone="info" size="sm">
          managed
        </Badge>
      ) : null}
      {onToggleManaged ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onToggleManaged(project.id, !project.managed)}
          data-testid={`active-project-${project.id}-toggle`}
        >
          {project.managed ? "managed 해제" : "managed 지정"}
        </Button>
      ) : null}
    </li>
  );
}

function PanelLoading() {
  return (
    <div
      role="status"
      aria-label="Active Projects 로딩 중"
      data-testid="active-projects-loading"
      className="flex flex-col gap-2"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse items-center gap-3 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-3"
        >
          <span className="h-2 w-2 rounded-full bg-(--border)" />
          <span className="h-4 w-48 rounded-(--radius-sm) bg-(--border)" />
          <span className="ml-auto h-4 w-16 rounded-(--radius-pill) bg-(--border)" />
        </div>
      ))}
    </div>
  );
}

function PanelEmpty() {
  return (
    <div data-testid="active-projects-empty">
      <EmptyState
        title="아직 managed 프로젝트가 없어요"
        description="dreaming 사이클이 cluster 채택 임계를 통과하면 여기에 표시됩니다."
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
      className="flex flex-col items-start gap-2 rounded-(--radius-m) border border-(--color-error) bg-(--color-error-bg) p-3 text-sm"
    >
      <span className="font-medium text-(--color-error)">{message}</span>
      <p className="text-xs text-(--muted-foreground)">
        dreaming 사이드카 (`active_projects.jsonl`) 가 잠시 잠겼을 수 있어요. 잠시
        후 자동 재시도되지만, 즉시 다시 시도하려면 아래 버튼을 누르세요.
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

/**
 * "Nm/Nh/Nd ago" — now - updatedAt 의 분/시/일 단위 변환.
 * 음수(미래 시각)나 1분 미만은 "방금" — 시계 드리프트 방어.
 */
export function formatRelative(updatedAt: string, now = Date.now()): string {
  const ts = new Date(updatedAt).getTime();
  if (Number.isNaN(ts)) return "—";
  const diffMs = Math.max(0, now - ts);
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
