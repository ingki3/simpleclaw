/**
 * RecipeCard — 레시피 한 건 (admin.pen `GnNLO` 우측 목록).
 *
 * 구성:
 *  1) 헤더 — 이름 + version 뱃지 + enabled Switch
 *  2) 한 줄 설명 + trigger
 *  3) 단계 미리보기 — 최대 3 step 까지 인라인, 그 외는 ＋N more
 *  4) 푸터 — 의존 스킬 chip + timeout
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Switch } from "@/design/atoms/Switch";
import { cn } from "@/lib/cn";
import type { Recipe } from "../_data";

interface RecipeCardProps {
  recipe: Recipe;
  onToggleEnabled: (id: string, next: boolean) => void;
  className?: string;
}

const STEP_PREVIEW_LIMIT = 3;

export function RecipeCard({
  recipe,
  onToggleEnabled,
  className,
}: RecipeCardProps) {
  const previewSteps = recipe.steps.slice(0, STEP_PREVIEW_LIMIT);
  const overflow = recipe.steps.length - previewSteps.length;
  return (
    <article
      data-testid={`recipe-card-${recipe.id}`}
      data-enabled={recipe.enabled || undefined}
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border bg-(--card) p-5 shadow-(--shadow-sm)",
        recipe.enabled
          ? "border-(--border)"
          : "border-dashed border-(--border)",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-base font-semibold text-(--foreground-strong)">
            /{recipe.name}
          </h3>
          <Badge tone="info" size="sm">
            {recipe.version}
          </Badge>
        </div>
        <Switch
          checked={recipe.enabled}
          onCheckedChange={(next) => onToggleEnabled(recipe.id, next)}
          label={`${recipe.name} 활성화`}
          data-testid={`recipe-card-${recipe.id}-toggle`}
        />
      </header>

      <p className="text-sm text-(--muted-foreground)">{recipe.description}</p>

      <div className="flex flex-col gap-1 text-xs text-(--muted-foreground)">
        <div className="flex items-baseline gap-2">
          <span className="w-16 shrink-0 uppercase tracking-wide">trigger</span>
          <span className="font-mono text-(--foreground)">{recipe.trigger}</span>
        </div>
      </div>

      <ol
        data-testid={`recipe-card-${recipe.id}-steps`}
        className="flex flex-col gap-1 rounded-(--radius-m) border border-(--border) bg-(--surface) p-3"
      >
        {previewSteps.map((step, idx) => (
          <li
            key={`${step.name}-${idx}`}
            className="flex items-baseline gap-2 text-xs"
          >
            <span className="font-mono text-(--muted-foreground)">
              {idx + 1}.
            </span>
            <span className="font-medium text-(--foreground)">{step.name}</span>
            <span className="ml-1 truncate text-(--muted-foreground)">
              {step.summary}
            </span>
          </li>
        ))}
        {overflow > 0 ? (
          <li className="text-xs text-(--muted-foreground)">
            ＋ {overflow} 단계 더보기
          </li>
        ) : null}
      </ol>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-(--border) pt-3">
        <div className="flex flex-wrap items-center gap-1">
          {recipe.skills.map((id) => (
            <span
              key={id}
              data-testid={`recipe-card-${recipe.id}-dep-${id}`}
              className="rounded-(--radius-sm) bg-(--surface) px-1.5 py-0.5 font-mono text-[11px] text-(--muted-foreground)"
            >
              {id}
            </span>
          ))}
        </div>
        <span className="font-mono text-xs text-(--muted-foreground)">
          timeout {recipe.timeoutSeconds}s
        </span>
      </footer>
    </article>
  );
}
