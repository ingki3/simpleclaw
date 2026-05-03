"use client";

/**
 * RecipeCard — 레시피 목록의 한 칸 + 단계별 보기.
 *
 * BIZ-47 §레시피 탭: 목록 + 단계별 보기 + 활성 토글.
 * Skills 카드와 달리 본 카드는 펼침/접기 토글로 단계 목록을 inline에 노출한다 —
 * 레시피 단계는 평균 2–5개로 짧고, 별도 Drawer를 띄우면 비교가 어렵다는 판단.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, Layers, Wrench, Terminal, MessageSquare } from "lucide-react";
import { cn } from "@/lib/cn";
import { Switch } from "@/components/atoms/Switch";
import { Badge } from "@/components/atoms/Badge";
import type { Recipe, RecipeStep } from "@/lib/skills-types";

const STEP_ICON: Record<RecipeStep["type"], typeof Wrench> = {
  skill: Wrench,
  command: Terminal,
  prompt: MessageSquare,
  instruction: MessageSquare,
};

export interface RecipeCardProps {
  recipe: Recipe;
  onToggleEnabled: (id: string, next: boolean) => void;
}

export function RecipeCard({ recipe, onToggleEnabled }: RecipeCardProps) {
  const [open, setOpen] = useState(false);
  return (
    <article className="flex flex-col gap-3 rounded-[--radius-l] border border-[--border] bg-[--card] p-4">
      <header className="flex items-start justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={`recipe-steps-${recipe.id}`}
          className="flex min-w-0 flex-1 items-start gap-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-[--ring] rounded-[--radius-m]"
        >
          <span aria-hidden className="mt-0.5 text-[--muted-foreground]">
            {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-sm font-semibold text-[--foreground-strong]">
                /{recipe.name}
              </h3>
              <Badge tone={recipe.version === "v2" ? "brand" : "neutral"}>
                {recipe.version}
              </Badge>
              {recipe.skills.length > 0 ? (
                <Badge tone="neutral">
                  <Layers size={10} className="mr-1" aria-hidden />
                  스킬 {recipe.skills.length}
                </Badge>
              ) : null}
            </div>
            <p className="line-clamp-2 text-xs text-[--muted-foreground]">
              {recipe.description}
            </p>
            <p className="text-xs text-[--muted-foreground]">
              <span className="font-mono">trigger:</span> {recipe.trigger}
            </p>
          </div>
        </button>
        <span className="shrink-0">
          <Switch
            checked={recipe.enabled}
            onCheckedChange={(next) => onToggleEnabled(recipe.id, next)}
            label={`${recipe.name} ${recipe.enabled ? "비활성화" : "활성화"}`}
          />
        </span>
      </header>

      {open ? (
        <ol
          id={`recipe-steps-${recipe.id}`}
          className="ml-6 flex flex-col gap-2 border-l border-[--border] pl-4"
        >
          {recipe.steps.map((step, i) => {
            const Icon = STEP_ICON[step.type];
            return (
              <li
                key={`${recipe.id}-step-${i}`}
                className="flex items-start gap-2 text-xs text-[--foreground]"
              >
                <span
                  aria-hidden
                  className={cn(
                    "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-[--radius-pill] bg-[--surface] text-[--muted-foreground]",
                  )}
                >
                  <Icon size={11} />
                </span>
                <div className="flex flex-1 flex-col">
                  <span className="font-medium text-[--foreground-strong]">
                    {i + 1}. {step.name}
                  </span>
                  <span className="text-[--muted-foreground]">
                    {step.summary}
                  </span>
                </div>
                <Badge tone="neutral">{step.type}</Badge>
              </li>
            );
          })}
          <li className="text-[10px] text-[--muted-foreground]">
            timeout {recipe.timeout_seconds}s
          </li>
        </ol>
      ) : null}
    </article>
  );
}
