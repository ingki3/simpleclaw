"use client";

/**
 * SecretField — Atomic. 마스킹 표시 + reveal/copy/rotate 액션 (DESIGN.md §3.1, §4.2).
 *
 * SimpleClaw 전용 — 시크릿 값은 항상 마스킹되어 화면에 그려지고,
 * reveal 클릭 시 카운트다운(기본 30s) 후 자동 마스킹된다.
 *
 * 본 컴포넌트는 *시각/UX 행위* 만 담당하고, 실제 시크릿 fetch/rotate API 호출은
 * 부모가 prop 콜백으로 주입한다 — 보안 경계 분리.
 */

import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";

export interface SecretFieldProps {
  /** 마스킹 표시용 prefix (예: 마지막 4자리 `••••1234`). */
  maskedPreview: string;
  /** reveal 시 표시할 실제 값 — 부모가 즉시 알고 있을 때만 prop 으로 전달. */
  revealedValue?: string;
  /** reveal 시도 시 호출 — 부모가 비동기 fetch 후 revealedValue 를 갱신. */
  onReveal?: () => void;
  onCopy?: () => void;
  onRotate?: () => void;
  /** reveal 자동 만료 초. 기본 30. */
  revealSeconds?: number;
  className?: string;
}

export function SecretField({
  maskedPreview,
  revealedValue,
  onReveal,
  onCopy,
  onRotate,
  revealSeconds = 30,
  className,
}: SecretFieldProps) {
  const [revealed, setRevealed] = useState(false);
  const [remaining, setRemaining] = useState(revealSeconds);

  useEffect(() => {
    if (!revealed) return;
    if (remaining <= 0) {
      setRevealed(false);
      return;
    }
    const id = window.setTimeout(() => setRemaining((s) => s - 1), 1000);
    return () => window.clearTimeout(id);
  }, [revealed, remaining]);

  const handleReveal = () => {
    onReveal?.();
    setRemaining(revealSeconds);
    setRevealed(true);
  };

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-(--radius-m) bg-(--secret-mask-bg) px-3 py-1.5 font-mono text-sm",
        className,
      )}
    >
      <span aria-live="polite">
        {revealed && revealedValue ? revealedValue : maskedPreview}
      </span>
      {revealed ? (
        <span
          aria-hidden
          className="text-xs text-(--muted-foreground) tabular-nums"
        >
          {remaining}s
        </span>
      ) : null}
      <span className="ml-auto inline-flex items-center gap-1">
        {onReveal ? (
          <button
            type="button"
            onClick={handleReveal}
            className="rounded-(--radius-sm) px-1.5 py-0.5 text-xs text-(--primary) hover:bg-(--primary-tint)"
          >
            {revealed ? "다시 가리기" : "보기"}
          </button>
        ) : null}
        {onCopy ? (
          <button
            type="button"
            onClick={onCopy}
            className="rounded-(--radius-sm) px-1.5 py-0.5 text-xs text-(--muted-foreground) hover:bg-(--surface)"
          >
            복사
          </button>
        ) : null}
        {onRotate ? (
          <button
            type="button"
            onClick={onRotate}
            className="rounded-(--radius-sm) px-1.5 py-0.5 text-xs text-(--color-warning) hover:bg-(--color-warning-bg)"
          >
            회전
          </button>
        ) : null}
      </span>
    </span>
  );
}
