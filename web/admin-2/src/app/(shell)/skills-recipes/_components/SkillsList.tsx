/**
 * SkillsList — 설치된 스킬 카드 목록 + 4-variant (default/empty/loading/error).
 *
 * DESIGN.md §1 Principle 3 — 모든 영역에 4-variant 시각 박제.
 * llm-router 의 ProvidersGrid 패턴을 그대로 따른다.
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { InstalledSkill } from "../_data";
import { SkillCard } from "./SkillCard";

export type SkillsListState = "default" | "empty" | "loading" | "error";

interface SkillsListProps {
  state: SkillsListState;
  skills?: readonly InstalledSkill[];
  /** 카드 Switch — 부모가 state mutation 담당. */
  onToggleEnabled: (id: string, next: boolean) => void;
  /** 카드 "정책 편집" — Retry Policy 모달을 연다. */
  onEditPolicy: (skill: InstalledSkill) => void;
  /** Discovery Drawer 트리거 — empty CTA 도 동일 액션. */
  onDiscover: () => void;
  /** error 시 노출할 사람 친화 메시지. */
  errorMessage?: string;
  onRetry?: () => void;
  /** 검색어 — 빈 결과 시 안내 문구를 다르게 노출. */
  searchQuery?: string;
  className?: string;
}

const SKELETON_COUNT = 3;

export function SkillsList({
  state,
  skills = [],
  onToggleEnabled,
  onEditPolicy,
  onDiscover,
  errorMessage = "스킬 목록을 불러오지 못했습니다.",
  onRetry,
  searchQuery,
  className,
}: SkillsListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="skills-list"
      data-state={state}
      aria-label="설치된 스킬"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? (
        <ListEmpty onDiscover={onDiscover} filtered={false} />
      ) : null}
      {state === "default" ? (
        skills.length === 0 ? (
          <ListEmpty onDiscover={onDiscover} filtered={isFiltered} />
        ) : (
          <div
            data-testid="skills-list-grid"
            className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          >
            {skills.map((s) => (
              <SkillCard
                key={s.id}
                skill={s}
                onToggleEnabled={onToggleEnabled}
                onEditPolicy={onEditPolicy}
              />
            ))}
          </div>
        )
      ) : null}
    </section>
  );
}

function ListLoading() {
  return (
    <div
      role="status"
      aria-label="스킬 목록 로딩 중"
      data-testid="skills-list-loading"
      className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex h-[220px] animate-pulse flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5"
        >
          <div className="h-5 w-24 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-3/4 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-2/3 rounded-(--radius-sm) bg-(--surface)" />
          <div className="mt-auto h-9 w-full rounded-(--radius-m) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({
  onDiscover,
  filtered,
}: {
  onDiscover: () => void;
  filtered: boolean;
}) {
  // 검색 결과 0 vs 진짜 빈 상태를 다르게 안내한다.
  if (filtered) {
    return (
      <div data-testid="skills-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="검색 결과가 없어요"
          description="다른 키워드로 다시 시도하거나, 카탈로그에서 새 스킬을 찾아보세요."
          action={
            <Button size="sm" variant="secondary" onClick={onDiscover}>
              카탈로그 열기
            </Button>
          }
        />
      </div>
    );
  }
  return (
    <div data-testid="skills-list-empty" data-empty-reason="none">
      <EmptyState
        title="설치된 스킬이 없어요"
        description="`.agent/skills/` 또는 `~/.agents/skills/` 에 SKILL.md 를 추가하거나, 카탈로그에서 새 스킬을 추가하세요."
        action={
          <Button size="sm" variant="primary" onClick={onDiscover}>
            카탈로그 열기
          </Button>
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
      data-testid="skills-list-error"
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
          data-testid="skills-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
