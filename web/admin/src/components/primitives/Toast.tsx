"use client";

/**
 * Toast — DESIGN.md §3.2 / §4 Patterns. 우상단 1개 슬롯, 5초 후 자동 dismiss.
 *
 * 본 1차 구현은 BIZ-46 한정으로 페이지 내부 useState 기반 단발 토스트만 제공한다.
 * BIZ-43에서 글로벌 ToastProvider가 들어오면 본 컴포넌트는 그쪽으로 흡수될 수 있다.
 *
 * `undo` prop이 있으면 5분 윈도 동안 [되돌리기] 버튼을 노출한다 — 페르소나 저장
 * 흐름의 핵심이며, 운영자가 실수했을 때 단 한 번 직전 상태로 회귀한다.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Check, X, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/atoms/Button";

export type ToastTone = "success" | "info" | "warn" | "destructive-soft";

export interface ToastProps {
  tone?: ToastTone;
  title: string;
  description?: ReactNode;
  /** 자동 닫힘 ms — 0이면 자동 닫힘 비활성. */
  autoCloseMs?: number;
  /** 5분 윈도 undo. 클릭 시 한 번만 호출되고 토스트는 닫힌다. */
  undo?: {
    label?: string;
    /** undo 만료까지의 ms — 기본 5분. */
    windowMs?: number;
    onUndo: () => void | Promise<void>;
  };
  onClose: () => void;
}

const TONE: Record<
  ToastTone,
  { wrap: string; icon: ReactNode }
> = {
  success: {
    wrap: "border-[--color-success] bg-[--color-success-bg]",
    icon: <Check size={16} className="text-[--color-success]" aria-hidden />,
  },
  info: {
    wrap: "border-[--color-info] bg-[--color-info-bg]",
    icon: <Info size={16} className="text-[--color-info]" aria-hidden />,
  },
  warn: {
    wrap: "border-[--color-warning] bg-[--color-warning-bg]",
    icon: (
      <AlertTriangle size={16} className="text-[--color-warning]" aria-hidden />
    ),
  },
  "destructive-soft": {
    wrap: "border-[--color-error] bg-[--color-error-bg]",
    icon: (
      <AlertTriangle size={16} className="text-[--color-error]" aria-hidden />
    ),
  },
};

export function Toast({
  tone = "success",
  title,
  description,
  autoCloseMs = 5000,
  undo,
  onClose,
}: ToastProps) {
  const [undoUsed, setUndoUsed] = useState(false);

  // undo 윈도가 활성일 동안에는 자동 닫힘을 비활성한다 — 운영자가 보고 결정해야 함.
  useEffect(() => {
    if (autoCloseMs <= 0) return;
    if (undo) return; // undo 윈도 동안에는 사용자가 닫거나 만료될 때까지 유지
    const t = setTimeout(onClose, autoCloseMs);
    return () => clearTimeout(t);
  }, [autoCloseMs, onClose, undo]);

  // undo 윈도 만료 시 자동 닫힘
  useEffect(() => {
    if (!undo) return;
    const t = setTimeout(() => onClose(), undo.windowMs ?? 5 * 60 * 1000);
    return () => clearTimeout(t);
  }, [undo, onClose]);

  const tokens = TONE[tone];

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "pointer-events-auto fixed right-6 top-6 z-50 flex w-[360px] items-start gap-3 rounded-[--radius-m] border bg-[--card] px-4 py-3 text-sm shadow-[--shadow-m]",
        tokens.wrap,
      )}
    >
      <span className="mt-0.5 shrink-0">{tokens.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="font-medium text-[--foreground-strong]">{title}</div>
        {description ? (
          <div className="mt-0.5 text-xs text-[--muted-foreground]">
            {description}
          </div>
        ) : null}
        {undo ? (
          <div className="mt-2">
            <Button
              variant="outline"
              size="sm"
              disabled={undoUsed}
              onClick={async () => {
                setUndoUsed(true);
                await undo.onUndo();
                onClose();
              }}
            >
              {undo.label ?? "되돌리기"}
            </Button>
          </div>
        ) : null}
      </div>
      <button
        type="button"
        aria-label="닫기"
        onClick={onClose}
        className="shrink-0 rounded-[--radius-sm] p-1 text-[--muted-foreground] hover:bg-[--surface]"
      >
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}
