"use client";

/**
 * PersonaEditor — Domain. 마크다운 에디터 + 토큰 미터 + diff 슬롯 (DESIGN.md §3.4).
 *
 * S1 단계에서는 *시각 박제* 만 — 실제 마크다운 syntax highlight 와 diff 계산은
 * S3 (Persona 화면) 에서 라이브러리(react-markdown, diff-match-patch) 로 결합된다.
 * 본 컴포넌트는 좌측 에디터(textarea) + 우측 토큰 미터 + 하단 diff 슬롯의 레이아웃을 박제.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Textarea } from "../atoms/Textarea";

export interface PersonaEditorProps {
  value: string;
  onChange: (next: string) => void;
  /** 현재 토큰 사용량. */
  tokensCurrent: number;
  /** 토큰 예산 (한도). */
  tokensBudget: number;
  /** diff preview 영역 — 부모가 직접 그린다 (DryRunCard, 마크다운 diff 등). */
  diffPreview?: ReactNode;
  /** 우측 메타 슬롯 (작성자, 마지막 수정 시각). */
  meta?: ReactNode;
  className?: string;
}

export function PersonaEditor({
  value,
  onChange,
  tokensCurrent,
  tokensBudget,
  diffPreview,
  meta,
  className,
}: PersonaEditorProps) {
  const ratio = tokensBudget > 0 ? tokensCurrent / tokensBudget : 0;
  const tone =
    ratio >= 1
      ? "text-(--color-error)"
      : ratio >= 0.85
        ? "text-(--color-warning)"
        : "text-(--muted-foreground)";

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-4",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-(--foreground-strong)">
          Persona
        </h3>
        <div className="flex items-center gap-3 text-xs">
          {meta ? <span className="text-(--muted-foreground)">{meta}</span> : null}
          <span className={cn("tabular-nums", tone)}>
            {tokensCurrent.toLocaleString()} / {tokensBudget.toLocaleString()}{" "}
            tokens
          </span>
        </div>
      </header>
      <div
        aria-hidden
        className="h-1 w-full overflow-hidden rounded-(--radius-pill) bg-(--surface)"
      >
        <div
          className={cn(
            "h-full rounded-(--radius-pill) transition-all",
            ratio >= 1
              ? "bg-(--color-error)"
              : ratio >= 0.85
                ? "bg-(--color-warning)"
                : "bg-(--primary)",
          )}
          style={{ width: `${Math.min(ratio, 1) * 100}%` }}
        />
      </div>
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoGrow
        className="min-h-[200px] font-mono"
        aria-label="Persona markdown"
      />
      {diffPreview ? <div>{diffPreview}</div> : null}
    </div>
  );
}
