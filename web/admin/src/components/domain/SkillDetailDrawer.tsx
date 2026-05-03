"use client";

/**
 * SkillDetailDrawer — 우측 Drawer에 SKILL.md 미리보기 + 실행 로그 + 재시도 정책 편집을 담는다.
 *
 * BIZ-47 §스킬 상세(우 Drawer):
 *  - SKILL.md 미리보기 (코드 블록 그대로 — 1차에는 마크다운 렌더러 없이 ``<pre>``로)
 *  - 실행 로그 ``/admin/v1/skills/{id}/runs?limit=20`` (가상 스크롤)
 *  - 재시도 정책 편집(저장 시 PATCH)
 */

import { useEffect, useMemo, useState } from "react";
import { Save } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { Badge } from "@/components/atoms/Badge";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";
import { Drawer } from "@/components/molecules/Drawer";
import { VirtualLogList } from "@/components/molecules/VirtualLogList";
import { listSkillRuns } from "@/lib/skills-api";
import type {
  RetryPolicy,
  SkillDetail,
  SkillRun,
  SkillRunStatus,
} from "@/lib/skills-types";
import { Switch } from "@/components/atoms/Switch";
import { formatRelative } from "@/components/domain/SkillCard";

const STATUS_TONE: Record<SkillRunStatus, StatusTone> = {
  ok: "success",
  error: "error",
  timeout: "warning",
  skipped: "neutral",
};

const BACKOFF_OPTIONS: RetryPolicy["backoff_strategy"][] = [
  "none",
  "fixed",
  "linear",
  "exponential",
];

export interface SkillDetailDrawerProps {
  skill: SkillDetail | null;
  onClose: () => void;
  onToggleEnabled: (id: string, next: boolean) => void;
  onSavePolicy: (id: string, policy: RetryPolicy) => Promise<void> | void;
}

export function SkillDetailDrawer({
  skill,
  onClose,
  onToggleEnabled,
  onSavePolicy,
}: SkillDetailDrawerProps) {
  const [runs, setRuns] = useState<SkillRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [policy, setPolicy] = useState<RetryPolicy | null>(null);
  const [saving, setSaving] = useState(false);

  // 스킬이 바뀌면 실행 로그를 다시 받고, 정책 폼을 초기값으로 리셋한다.
  useEffect(() => {
    if (!skill) {
      setRuns([]);
      setPolicy(null);
      return;
    }
    setPolicy(skill.retry_policy);
    setRunsLoading(true);
    let cancelled = false;
    listSkillRuns(skill.id, 20)
      .then((data) => {
        if (!cancelled) setRuns(data);
      })
      .catch(() => {
        if (!cancelled) setRuns([]);
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [skill]);

  const dirty = useMemo(() => {
    if (!skill || !policy) return false;
    const a = skill.retry_policy;
    return (
      a.max_attempts !== policy.max_attempts ||
      a.backoff_seconds !== policy.backoff_seconds ||
      a.backoff_strategy !== policy.backoff_strategy
    );
  }, [skill, policy]);

  const open = Boolean(skill);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={skill?.name ?? ""}
      subtitle={skill?.directory}
      headerRight={
        skill ? (
          <Switch
            checked={skill.enabled}
            onCheckedChange={(next) => onToggleEnabled(skill.id, next)}
            label={`${skill.name} ${skill.enabled ? "비활성화" : "활성화"}`}
          />
        ) : null
      }
      footer={
        skill && policy ? (
          <Button
            variant="primary"
            size="sm"
            disabled={!dirty || saving}
            leftIcon={<Save size={14} aria-hidden />}
            onClick={async () => {
              setSaving(true);
              try {
                await onSavePolicy(skill.id, policy);
              } finally {
                setSaving(false);
              }
            }}
          >
            {saving ? "저장 중…" : "정책 저장"}
          </Button>
        ) : null
      }
    >
      {skill && policy ? (
        <div className="flex flex-col gap-6">
          {/* 메타 요약 */}
          <section className="flex flex-wrap items-center gap-2">
            <Badge tone={skill.source === "local" ? "brand" : "neutral"}>
              {skill.source === "local" ? "로컬" : "글로벌"}
            </Badge>
            {skill.user_invocable ? (
              <Badge tone="info">사용자 호출 가능</Badge>
            ) : null}
            {skill.argument_hint ? (
              <span className="text-xs text-[--muted-foreground]">
                arg: <code className="font-mono">{skill.argument_hint}</code>
              </span>
            ) : null}
          </section>

          {/* SKILL.md 미리보기 */}
          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[--muted-foreground]">
              SKILL.md
            </h3>
            <pre className="max-h-[280px] overflow-auto rounded-[--radius-m] border border-[--border] bg-[--surface] p-4 font-mono text-xs leading-relaxed text-[--foreground]">
              {skill.skill_md}
            </pre>
          </section>

          {/* 재시도 정책 편집 */}
          <section className="flex flex-col gap-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[--muted-foreground]">
              재시도 정책
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs text-[--muted-foreground]">
                최대 시도 횟수
                <Input
                  type="number"
                  min={1}
                  max={10}
                  value={policy.max_attempts}
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      max_attempts: Math.max(1, Number(e.target.value) || 1),
                    })
                  }
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-[--muted-foreground]">
                백오프 (초)
                <Input
                  type="number"
                  min={0}
                  max={300}
                  value={policy.backoff_seconds}
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      backoff_seconds: Math.max(
                        0,
                        Number(e.target.value) || 0,
                      ),
                    })
                  }
                />
              </label>
              <label className="col-span-2 flex flex-col gap-1 text-xs text-[--muted-foreground]">
                백오프 전략
                <select
                  value={policy.backoff_strategy}
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      backoff_strategy: e.target
                        .value as RetryPolicy["backoff_strategy"],
                    })
                  }
                  className="w-full rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2 text-sm text-[--foreground] outline-none focus:border-[--primary]"
                >
                  {BACKOFF_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="text-[11px] text-[--muted-foreground]">
              재시도는 BIZ-21에서 도입된 정책과 동일한 의미입니다. ``none``은 즉시 1회만 시도.
            </p>
          </section>

          {/* 실행 로그 (가상 스크롤) */}
          <section className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-[--muted-foreground]">
                실행 로그 (최근 20)
              </h3>
              {runsLoading ? (
                <span className="text-[11px] text-[--muted-foreground]">
                  불러오는 중…
                </span>
              ) : (
                <span className="text-[11px] text-[--muted-foreground]">
                  {runs.length}건
                </span>
              )}
            </div>
            <VirtualLogList
              items={runs}
              height={280}
              rowHeight={44}
              getKey={(r) => r.id}
              emptyState={
                <div className="text-center">
                  <p className="font-medium text-[--foreground-strong]">
                    아직 실행 이력이 없어요
                  </p>
                  <p className="mt-1 text-xs text-[--muted-foreground]">
                    스킬이 첫 호출되면 여기에 결과가 쌓입니다.
                  </p>
                </div>
              }
              renderRow={(run) => (
                <div className="flex h-full items-center gap-3 border-b border-[--border] px-3 text-xs last:border-b-0">
                  <StatusPill tone={STATUS_TONE[run.status]}>
                    {run.status}
                  </StatusPill>
                  <span className="w-20 shrink-0 text-[--muted-foreground]">
                    {formatRelative(run.started_at)}
                  </span>
                  <code
                    className="min-w-0 flex-1 truncate font-mono text-[--foreground]"
                    title={run.command}
                  >
                    {run.command}
                  </code>
                  <span className="shrink-0 text-[--muted-foreground]">
                    {Math.round(run.duration_ms)}ms
                  </span>
                  {run.attempt > 1 ? (
                    <Badge tone="warning">재시도 #{run.attempt}</Badge>
                  ) : null}
                </div>
              )}
            />
          </section>
        </div>
      ) : null}
    </Drawer>
  );
}
