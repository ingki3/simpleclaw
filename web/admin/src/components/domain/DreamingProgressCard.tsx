"use client";

/**
 * DreamingProgressCard — 드리밍 트리거 + 5단계 stepper.
 *
 * 진행 중에는 트리거 버튼이 disabled. 폴링으로 받은 ``DreamingState``를 그대로 시각화.
 * DESIGN.md §4.9 RestartBanner 5단계 구조를 단순화한 변형 — 이쪽은 완료 후 자동 사라지지 않고
 * 마지막 결과 메시지를 다음 회차 시작 전까지 노출한다.
 */

import { Sparkles, Loader2, CircleCheck, CircleAlert } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { cn } from "@/lib/cn";
import type { DreamingState } from "@/lib/api/memory";

const STEP_COUNT = 5;

const STEP_NAMES = [
  "준비 중",
  "메시지 클러스터링",
  "LLM 요약",
  "MEMORY.md 갱신",
  "완료 처리",
];

export interface DreamingProgressCardProps {
  state: DreamingState;
  onTrigger: () => void | Promise<void>;
  /** 외부에서 강제로 disable — 예: 인덱스 로드 실패 등. */
  disabled?: boolean;
}

export function DreamingProgressCard({
  state,
  onTrigger,
  disabled,
}: DreamingProgressCardProps) {
  const running = state.running;
  const currentStep = state.step ?? -1;

  return (
    <section
      aria-labelledby="dreaming-card-title"
      className="flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5"
    >
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2
            id="dreaming-card-title"
            className="flex items-center gap-2 text-sm font-semibold text-(--foreground-strong)"
          >
            <Sparkles size={14} aria-hidden /> 드리밍
          </h2>
          <p className="mt-1 text-xs text-(--muted-foreground)">
            대화 이력을 요약해 MEMORY.md에 새 항목을 추가합니다.
            진행 중에는 다시 누를 수 없어요.
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={onTrigger}
          disabled={running || disabled}
          leftIcon={
            running ? (
              <Loader2 size={12} aria-hidden className="animate-spin" />
            ) : (
              <Sparkles size={12} aria-hidden />
            )
          }
        >
          {running ? "진행 중…" : "지금 드리밍 실행"}
        </Button>
      </header>

      {/* Stepper */}
      <ol
        role="list"
        aria-label="드리밍 진행 단계"
        className="flex items-center gap-1.5 text-[10px] text-(--muted-foreground)"
      >
        {STEP_NAMES.map((label, idx) => {
          const isDone = running && idx < currentStep;
          const isActive = running && idx === currentStep;
          return (
            <li
              key={label}
              className={cn(
                "flex flex-1 flex-col items-center gap-1",
                "rounded-(--radius-sm) border px-1.5 py-1.5",
                isDone
                  ? "border-(--color-success) bg-(--color-success-bg) text-(--color-success)"
                  : isActive
                    ? "border-(--primary) bg-(--primary-tint) text-(--primary)"
                    : "border-(--border) bg-(--surface)",
              )}
              aria-current={isActive ? "step" : undefined}
            >
              <span className="font-mono text-[9px]">
                {idx + 1}/{STEP_COUNT}
              </span>
              <span className="truncate text-center">{label}</span>
            </li>
          );
        })}
      </ol>

      {/* 진행 중 라이브 라벨 */}
      {running && state.stepLabel ? (
        <p
          role="status"
          aria-live="polite"
          className="text-xs text-(--primary)"
        >
          {state.stepLabel}…
        </p>
      ) : null}

      {/* 직전 결과 */}
      {!running && state.lastFinishedAt ? (
        <div
          className={cn(
            "flex items-start gap-2 rounded-(--radius-m) border px-3 py-2 text-xs",
            state.lastOutcome === "success"
              ? "border-(--color-success) bg-(--color-success-bg) text-(--color-success)"
              : "border-(--color-error) bg-(--color-error-bg) text-(--color-error)",
          )}
        >
          {state.lastOutcome === "success" ? (
            <CircleCheck size={14} aria-hidden className="mt-0.5" />
          ) : (
            <CircleAlert size={14} aria-hidden className="mt-0.5" />
          )}
          <div>
            <div className="font-medium">
              {state.lastOutcome === "success" ? "최근 회차 완료" : "최근 회차 실패"}
            </div>
            <div className="text-(--muted-foreground)">
              {formatDateTime(state.lastFinishedAt)}
              {state.lastMessage ? ` · ${state.lastMessage}` : ""}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
