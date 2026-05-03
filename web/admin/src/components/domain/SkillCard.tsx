"use client";

/**
 * SkillCard — 스킬 목록의 한 칸. (BIZ-47 §스킬 목록 좌)
 *
 * 카드 표면 구성: 이름·설명·source 배지 / 활성 토글(우상단) / 마지막 실행 + 재시도 정책 배지(하단).
 *
 * 활성 토글은 dry-run 없이 즉시 호출자에게 위임한다 — DoD: "토글 시 즉시 반영(↻) + 토스트".
 * 카드 자체는 클릭 가능(상세 Drawer 열기)하므로, 토글의 ``onClick``은 propagation을 차단한다.
 */

import { Clock, RotateCw } from "lucide-react";
import { cn } from "@/lib/cn";
import { Switch } from "@/components/atoms/Switch";
import { Badge } from "@/components/atoms/Badge";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import type { Skill, SkillRunStatus } from "@/lib/skills-types";

const STATUS_TONE: Record<SkillRunStatus, StatusTone> = {
  ok: "success",
  error: "error",
  timeout: "warning",
  skipped: "neutral",
};

const STATUS_LABEL: Record<SkillRunStatus, string> = {
  ok: "성공",
  error: "실패",
  timeout: "타임아웃",
  skipped: "스킵",
};

export interface SkillCardProps {
  skill: Skill;
  selected?: boolean;
  onSelect: (id: string) => void;
  onToggleEnabled: (id: string, next: boolean) => void;
}

export function SkillCard({
  skill,
  selected,
  onSelect,
  onToggleEnabled,
}: SkillCardProps) {
  return (
    <article
      role="button"
      tabIndex={0}
      aria-label={`${skill.name} 상세 열기`}
      aria-pressed={selected || undefined}
      onClick={() => onSelect(skill.id)}
      onKeyDown={(e) => {
        // Enter / Space로도 카드 진입 가능 — 키보드 사용자 지원.
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(skill.id);
        }
      }}
      className={cn(
        "flex cursor-pointer flex-col gap-3 rounded-(--radius-l) border bg-(--card) p-4 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-(--ring)",
        selected
          ? "border-(--primary) shadow-(--shadow-sm)"
          : "border-(--border) hover:border-(--border-strong)",
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-(--foreground-strong)">
              {skill.name}
            </h3>
            <Badge tone={skill.source === "local" ? "brand" : "neutral"}>
              {skill.source === "local" ? "로컬" : "글로벌"}
            </Badge>
            {skill.user_invocable ? <Badge tone="info">/명령</Badge> : null}
          </div>
          <p className="line-clamp-2 text-xs text-(--muted-foreground)">
            {skill.description}
          </p>
        </div>
        {/* 토글은 카드 클릭과 분리 — Switch가 button이므로 native bubbling을 차단. */}
        <span
          className="shrink-0"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") e.stopPropagation();
          }}
        >
          <Switch
            checked={skill.enabled}
            onCheckedChange={(next) => onToggleEnabled(skill.id, next)}
            label={`${skill.name} ${skill.enabled ? "비활성화" : "활성화"}`}
          />
        </span>
      </header>

      <footer className="flex flex-wrap items-center gap-2 text-xs text-(--muted-foreground)">
        <span className="inline-flex items-center gap-1">
          <Clock size={12} aria-hidden />
          {skill.last_run ? formatRelative(skill.last_run.started_at) : "실행 이력 없음"}
        </span>
        {skill.last_run ? (
          <StatusPill tone={STATUS_TONE[skill.last_run.status]}>
            {STATUS_LABEL[skill.last_run.status]}
          </StatusPill>
        ) : null}
        <Badge tone="neutral">
          <RotateCw size={10} className="mr-1" aria-hidden />
          재시도 {skill.retry_policy.max_attempts}회 ·{" "}
          {skill.retry_policy.backoff_strategy}
        </Badge>
      </footer>
    </article>
  );
}

/**
 * ISO 시각을 한국어 상대시간으로. 정확도보다 가독성 — "방금" / "12분 전" / "2일 전".
 */
export function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso;
  const diffSec = Math.max(0, (Date.now() - ts) / 1000);
  if (diffSec < 30) return "방금";
  if (diffSec < 60 * 60) return `${Math.floor(diffSec / 60)}분 전`;
  if (diffSec < 60 * 60 * 24) return `${Math.floor(diffSec / 3600)}시간 전`;
  if (diffSec < 60 * 60 * 24 * 30) return `${Math.floor(diffSec / 86400)}일 전`;
  return new Date(iso).toISOString().slice(0, 10);
}
