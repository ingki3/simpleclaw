/**
 * SkillCard — admin.pen `GnNLO` 스킬 카드 한 장 (DESIGN.md §3.2 / §4.1).
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — 이름 + source(local/global) Badge + 우측 enabled Switch
 *  2) 한 줄 설명
 *  3) StatusPill + 마지막 실행 라벨
 *  4) 메타 행 — directory · 인자 힌트 · /명령 가능 여부
 *  5) 푸터 — Retry Policy 요약 + "정책 편집" 버튼
 *
 * 카드 자체는 클릭 안 됨 — Switch / 정책 편집 버튼만 트리거.
 */
"use client";

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { StatusPill } from "@/design/atoms/StatusPill";
import { Switch } from "@/design/atoms/Switch";
import { cn } from "@/lib/cn";
import type { InstalledSkill, RetryPolicy } from "../_data";

interface SkillCardProps {
  skill: InstalledSkill;
  /** 활성/비활성 토글 — 부모가 fixture/state 갱신을 담당. */
  onToggleEnabled: (id: string, next: boolean) => void;
  /** Retry Policy 모달 트리거. */
  onEditPolicy: (skill: InstalledSkill) => void;
  className?: string;
}

export function SkillCard({
  skill,
  onToggleEnabled,
  onEditPolicy,
  className,
}: SkillCardProps) {
  return (
    <article
      data-testid={`skill-card-${skill.id}`}
      data-enabled={skill.enabled || undefined}
      data-source={skill.source}
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border bg-(--card) p-5 shadow-(--shadow-sm)",
        skill.enabled ? "border-(--border)" : "border-dashed border-(--border)",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="truncate text-base font-semibold text-(--foreground-strong)">
            {skill.name}
          </h3>
          <Badge tone={skill.source === "local" ? "info" : "neutral"}>
            {skill.source}
          </Badge>
          {skill.userInvocable ? (
            <Badge tone="brand" size="sm">
              /명령
            </Badge>
          ) : null}
        </div>
        <Switch
          checked={skill.enabled}
          onCheckedChange={(next) => onToggleEnabled(skill.id, next)}
          label={`${skill.name} 활성화`}
          data-testid={`skill-card-${skill.id}-toggle`}
        />
      </header>

      <p className="text-sm text-(--muted-foreground)">{skill.description}</p>

      <div className="flex items-center gap-2">
        <StatusPill tone={skill.health.tone}>{skill.health.label}</StatusPill>
      </div>

      <dl className="flex flex-col gap-1 text-xs text-(--muted-foreground)">
        <div className="flex items-baseline gap-2">
          <dt className="w-16 shrink-0 uppercase tracking-wide">dir</dt>
          <dd className="truncate font-mono text-(--foreground)">
            {skill.directory}
          </dd>
        </div>
        {skill.argumentHint ? (
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 uppercase tracking-wide">args</dt>
            <dd className="truncate font-mono text-(--foreground)">
              {skill.argumentHint}
            </dd>
          </div>
        ) : null}
      </dl>

      <footer className="flex items-center justify-between gap-2 border-t border-(--border) pt-3">
        <span
          data-testid={`skill-card-${skill.id}-policy`}
          className="font-mono text-xs text-(--muted-foreground)"
        >
          {summarizePolicy(skill.retryPolicy)}
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onEditPolicy(skill)}
          data-testid={`skill-card-${skill.id}-edit-policy`}
        >
          정책 편집
        </Button>
      </footer>
    </article>
  );
}

/** Retry policy 한 줄 요약 — 카드 푸터의 SSOT. */
function summarizePolicy(policy: RetryPolicy): string {
  const strategy =
    policy.backoffStrategy === "none"
      ? "no-backoff"
      : `${policy.backoffStrategy} ${policy.backoffSeconds}s`;
  return `재시도 ${policy.maxAttempts}회 · ${strategy} · ${policy.timeoutSeconds}s timeout`;
}
