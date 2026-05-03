"use client";

/**
 * RestartStepper — DESIGN.md §4.9 Restart Required.
 *
 * 데몬 재시작 흐름의 5단계 stepper 모달.
 *
 *   1) 변경 대기 (pending)        — 누적된 변경 내역 요약
 *   2) Dry-run                   — 영향 분석/diff 미리보기
 *   3) 확정 (confirm)            — 운영자 의사 확인(즉시/다음 시작 시 적용)
 *   4) 적용 중 (applying)        — 데몬 재시작 진행
 *   5) 결과 (done)               — 헬스 회복 + 결과 요약 / 실패 시 롤백 안내
 *
 * 본 컴포넌트는 *프레젠테이션*만 책임진다. 단계 진행/롤백 결정은 호출자가 controlled
 * prop ``step``으로 주입하고, 각 단계에서 ``onAdvance / onCancel``를 받아 처리한다.
 *
 * 후속 이슈에서 ``/system/restart`` 호출 + 헬스 폴링 로직을 본 모달에 합성해
 * fully-managed 컴포넌트 ``<RestartFlow>``를 만들 수 있다.
 */

import { type ReactNode } from "react";
import { Check, Loader2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/atoms/Button";
import { Modal } from "./Modal";

export type RestartStep =
  | "pending"
  | "dry-run"
  | "confirm"
  | "applying"
  | "done";

const STEPS: ReadonlyArray<{ id: RestartStep; label: string }> = [
  { id: "pending", label: "변경 대기" },
  { id: "dry-run", label: "Dry-run" },
  { id: "confirm", label: "확정" },
  { id: "applying", label: "적용 중" },
  { id: "done", label: "결과" },
];

export interface RestartStepperProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 현재 단계. */
  step: RestartStep;
  /** "다음" 또는 단계별 1차 액션 (예: dry-run 트리거, 적용 시작) — null이면 버튼 숨김. */
  onAdvance?: () => void | Promise<void>;
  /** 1차 액션 라벨 — 단계별로 호출자가 지정 ("Dry-run", "지금 재시작" 등). */
  advanceLabel?: string;
  /** 1차 액션 비활성화. */
  advanceDisabled?: boolean;
  /** 결과 단계에서 마무리 버튼 라벨 — 기본 "확인". */
  doneLabel?: string;
  /** 결과 단계에서 실패 여부 — 표시 톤이 변한다. */
  failed?: boolean;
  /** 단계별 본문 슬롯. */
  children?: ReactNode;
}

export function RestartStepper({
  open,
  onOpenChange,
  step,
  onAdvance,
  advanceLabel,
  advanceDisabled,
  doneLabel = "확인",
  failed,
  children,
}: RestartStepperProps) {
  const currentIndex = STEPS.findIndex((s) => s.id === step);
  const dismissible = step !== "applying";

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="데몬 재시작"
      description="변경 사항을 안전하게 적용하기 위해 5단계를 거칩니다."
      size="md"
      dismissible={dismissible}
      footer={
        <>
          {step !== "applying" && step !== "done" && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              취소
            </Button>
          )}
          {step === "done" ? (
            <Button
              variant={failed ? "outline" : "primary"}
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              {doneLabel}
            </Button>
          ) : onAdvance ? (
            <Button
              variant="primary"
              size="sm"
              onClick={() => void onAdvance()}
              disabled={!!advanceDisabled || step === "applying"}
            >
              {step === "applying" ? "적용 중…" : advanceLabel ?? "다음"}
            </Button>
          ) : null}
        </>
      }
    >
      <ol
        aria-label="재시작 진행 단계"
        className="mb-5 flex items-center gap-2 text-xs text-(--muted-foreground)"
      >
        {STEPS.map((s, i) => {
          const state =
            i < currentIndex ? "done" : i === currentIndex ? "active" : "todo";
          const isFailedDone = failed && s.id === "done" && state !== "todo";
          return (
            <li
              key={s.id}
              className="flex flex-1 items-center gap-2"
              aria-current={state === "active" ? "step" : undefined}
            >
              <span
                className={cn(
                  "grid h-6 w-6 place-items-center rounded-(--radius-pill) border text-[10px] font-medium",
                  state === "done" &&
                    !isFailedDone &&
                    "border-(--color-success) bg-(--color-success-bg) text-(--color-success)",
                  state === "active" &&
                    "border-(--primary) bg-(--card) text-(--primary)",
                  state === "todo" &&
                    "border-(--border) bg-(--surface) text-(--muted-foreground)",
                  isFailedDone &&
                    "border-(--color-error) bg-(--color-error-bg) text-(--color-error)",
                )}
              >
                {state === "active" && step === "applying" ? (
                  <Loader2 size={12} className="animate-spin" aria-hidden />
                ) : isFailedDone ? (
                  <AlertTriangle size={12} aria-hidden />
                ) : state === "done" ? (
                  <Check size={12} aria-hidden />
                ) : (
                  i + 1
                )}
              </span>
              <span
                className={cn(
                  "truncate",
                  state === "active" && "font-medium text-(--foreground)",
                  isFailedDone && "text-(--color-error)",
                )}
              >
                {s.label}
              </span>
              {i < STEPS.length - 1 && (
                <span
                  aria-hidden
                  className={cn(
                    "h-px flex-1",
                    i < currentIndex
                      ? "bg-(--color-success)"
                      : "bg-(--border)",
                  )}
                />
              )}
            </li>
          );
        })}
      </ol>
      <div>{children}</div>
    </Modal>
  );
}
