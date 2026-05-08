/**
 * ThemeCard — admin.pen `riz1r` (cardTheme) 박제.
 *
 * Light / Dark / System segmented 라디오 + Reduce motion 토글.
 * ThemeProvider 의 mode 와 1:1 동기화. Reduce motion 은 본 단계에서 시각만 박제하고,
 * `prefers-reduced-motion` 매체 쿼리 정책은 후속 sub-issue 가 추가한다 (BIZ-63 trail).
 */
"use client";

import { useState } from "react";
import { Switch } from "@/design/atoms/Switch";
import { useTheme, type ThemeMode } from "@/design/ThemeProvider";
import { cn } from "@/lib/cn";

const THEME_OPTIONS: readonly { value: ThemeMode; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

interface ThemeCardProps {
  className?: string;
}

export function ThemeCard({ className }: ThemeCardProps) {
  const { mode, setMode } = useTheme();
  // Reduce motion 은 본 단계에서 카드 내부 state 만으로 박제.
  const [reduceMotion, setReduceMotion] = useState(false);

  return (
    <section
      data-testid="theme-card"
      aria-label="테마"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">테마</h2>
      <p className="text-xs text-(--muted-foreground)">
        BIZ-63 채택안 — System 모드는 OS 설정을 따릅니다.
      </p>

      <div
        role="radiogroup"
        aria-label="테마 모드"
        data-testid="theme-card-options"
        className="flex items-stretch gap-1 rounded-(--radius-m) border border-(--border) bg-(--surface) p-1"
      >
        {THEME_OPTIONS.map((opt) => {
          const active = mode === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              data-testid={`theme-card-option-${opt.value}`}
              data-active={active || undefined}
              onClick={() => setMode(opt.value)}
              className={cn(
                "flex-1 rounded-(--radius-m) px-3 py-1.5 text-xs font-medium transition-colors",
                active
                  ? "bg-(--card) text-(--foreground-strong) shadow-(--shadow-sm)"
                  : "text-(--muted-foreground) hover:text-(--foreground)",
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-(--muted-foreground)">Reduce motion</span>
        <Switch
          checked={reduceMotion}
          onCheckedChange={setReduceMotion}
          label="Reduce motion"
          data-testid="theme-card-reduce-motion"
        />
      </div>
    </section>
  );
}
