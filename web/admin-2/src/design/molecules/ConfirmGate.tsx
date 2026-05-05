"use client";

/**
 * ConfirmGate — Molecular. 텍스트 confirm 입력 + 카운트다운 (DESIGN.md §3.2, §4.2).
 *
 * 위험 등급 작업(시크릿 회전, 삭제) 전에 운영자가 키워드를 직접 입력해
 * 의도를 재확인한다. 카운트다운은 잠금 해제 게이지로 사용 — 짧은 hover 로
 * 실수 클릭을 차단.
 */

import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { Input } from "../atoms/Input";
import { Button } from "../atoms/Button";

export interface ConfirmGateProps {
  /** 운영자가 입력해야 할 키워드 (예: "rotate" 또는 자원 이름). */
  keyword: string;
  /** 키워드 + 카운트다운 모두 통과 시 호출. */
  onConfirm: () => void;
  onCancel?: () => void;
  /** 카운트다운 초. 기본 5. */
  countdownSeconds?: number;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 위에 표시할 안내문. */
  description?: React.ReactNode;
  className?: string;
}

export function ConfirmGate({
  keyword,
  onConfirm,
  onCancel,
  countdownSeconds = 5,
  confirmLabel = "실행",
  cancelLabel = "취소",
  description,
  className,
}: ConfirmGateProps) {
  const [typed, setTyped] = useState("");
  const [remaining, setRemaining] = useState(countdownSeconds);
  const matched = typed.trim() === keyword;

  // 키워드가 정확히 일치하기 시작한 시점부터 카운트다운.
  useEffect(() => {
    if (!matched) {
      setRemaining(countdownSeconds);
      return;
    }
    if (remaining <= 0) return;
    const id = window.setTimeout(() => setRemaining((s) => s - 1), 1000);
    return () => window.clearTimeout(id);
  }, [matched, remaining, countdownSeconds]);

  const ready = matched && remaining === 0;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--color-error) bg-(--color-error-bg) p-4",
        className,
      )}
    >
      {description ? (
        <p className="text-sm text-(--foreground)">{description}</p>
      ) : null}
      <label className="flex flex-col gap-1 text-sm text-(--foreground)">
        <span>
          확인을 위해 <code className="font-mono">{keyword}</code> 를 입력하세요.
        </span>
        <Input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={keyword}
          aria-label="confirm keyword"
        />
      </label>
      <div className="flex items-center justify-between gap-2">
        <span
          aria-live="polite"
          className="text-xs text-(--muted-foreground) tabular-nums"
        >
          {matched
            ? remaining === 0
              ? "준비 완료"
              : `${remaining}초 후 활성화`
            : "키워드 입력 대기"}
        </span>
        <div className="flex gap-2">
          {onCancel ? (
            <Button variant="ghost" size="sm" onClick={onCancel}>
              {cancelLabel}
            </Button>
          ) : null}
          <Button
            variant="destructive"
            size="sm"
            disabled={!ready}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
