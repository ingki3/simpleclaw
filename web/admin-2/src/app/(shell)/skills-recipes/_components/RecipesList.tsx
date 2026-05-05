/**
 * RecipesList — 레시피 카드 목록 + 4-variant.
 *
 * SkillsList 와 동일한 구조 — 레시피 단계 미리보기 카드는 가로 폭이 더 넓어
 * 그리드는 1~2 열까지만 (3 열은 답답).
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { EmptyState } from "@/design/molecules/EmptyState";
import { StatusPill } from "@/design/atoms/StatusPill";
import { cn } from "@/lib/cn";
import type { Recipe } from "../_data";
import { RecipeCard } from "./RecipeCard";

export type RecipesListState = "default" | "empty" | "loading" | "error";

interface RecipesListProps {
  state: RecipesListState;
  recipes?: readonly Recipe[];
  onToggleEnabled: (id: string, next: boolean) => void;
  errorMessage?: string;
  onRetry?: () => void;
  searchQuery?: string;
  className?: string;
}

const SKELETON_COUNT = 2;

export function RecipesList({
  state,
  recipes = [],
  onToggleEnabled,
  errorMessage = "레시피 목록을 불러오지 못했습니다.",
  onRetry,
  searchQuery,
  className,
}: RecipesListProps) {
  const isFiltered = Boolean(searchQuery && searchQuery.trim().length > 0);
  return (
    <section
      data-testid="recipes-list"
      data-state={state}
      aria-label="레시피 목록"
      aria-busy={state === "loading" || undefined}
      className={cn("flex flex-col gap-3", className)}
    >
      {state === "loading" ? <ListLoading /> : null}
      {state === "error" ? (
        <ListError message={errorMessage} onRetry={onRetry} />
      ) : null}
      {state === "empty" ? <ListEmpty filtered={false} /> : null}
      {state === "default" ? (
        recipes.length === 0 ? (
          <ListEmpty filtered={isFiltered} />
        ) : (
          <div
            data-testid="recipes-list-grid"
            className="grid grid-cols-1 gap-4 lg:grid-cols-2"
          >
            {recipes.map((r) => (
              <RecipeCard
                key={r.id}
                recipe={r}
                onToggleEnabled={onToggleEnabled}
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
      aria-label="레시피 목록 로딩 중"
      data-testid="recipes-list-loading"
      className="grid grid-cols-1 gap-4 lg:grid-cols-2"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div
          key={i}
          className="flex h-[220px] animate-pulse flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5"
        >
          <div className="h-5 w-32 rounded-(--radius-sm) bg-(--surface)" />
          <div className="h-3 w-3/4 rounded-(--radius-sm) bg-(--surface)" />
          <div className="mt-2 h-20 w-full rounded-(--radius-m) bg-(--surface)" />
        </div>
      ))}
    </div>
  );
}

function ListEmpty({ filtered }: { filtered: boolean }) {
  if (filtered) {
    return (
      <div data-testid="recipes-list-empty" data-empty-reason="filtered">
        <EmptyState
          title="검색 결과가 없어요"
          description="다른 키워드로 다시 시도해 보세요."
        />
      </div>
    );
  }
  return (
    <div data-testid="recipes-list-empty" data-empty-reason="none">
      <EmptyState
        title="등록된 레시피가 없어요"
        description="`recipes/` 에 YAML 을 추가하면 여기에 표시됩니다. 가장 흔한 출발은 매일 아침 브리핑이에요."
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
      data-testid="recipes-list-error"
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
          data-testid="recipes-list-retry"
        >
          다시 불러오기
        </Button>
      ) : null}
    </div>
  );
}
