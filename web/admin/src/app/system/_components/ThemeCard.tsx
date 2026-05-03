"use client";

/**
 * ThemeCard — System 화면의 테마(라이트/다크/시스템) 토글 카드.
 *
 * `ThemeProvider`(/lib/theme.tsx)가 이미 라이트/다크 토큰 swap과 prefers-color-scheme
 * 추종을 처리하므로, 본 카드는 세 가지 모드 중 하나를 선택하는 라디오 그룹 + 현재
 * 적용 모드(resolved) 표시만 담당한다. 변경 즉시 `<html>` 클래스가 토글되어 화면
 * 전체에 반영된다(↻ Hot).
 */

import { Sun, Moon, Monitor } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { SettingCard } from "@/components/molecules/SettingCard";
import { cn } from "@/lib/cn";
import { useTheme, type ThemeMode } from "@/lib/theme";

const OPTIONS: ReadonlyArray<{
  value: ThemeMode;
  label: string;
  hint: string;
  Icon: typeof Sun;
}> = [
  { value: "light", label: "라이트", hint: "항상 밝은 테마", Icon: Sun },
  { value: "dark", label: "다크", hint: "항상 어두운 테마", Icon: Moon },
  {
    value: "system",
    label: "시스템",
    hint: "운영체제 설정을 따라갑니다.",
    Icon: Monitor,
  },
];

export function ThemeCard() {
  const { mode, resolved, setMode } = useTheme();
  return (
    <SettingCard
      title="테마"
      description="라이트/다크 토글 또는 시스템 prefers-color-scheme을 따릅니다. 선택은 즉시 반영됩니다."
      headerRight={<Badge tone="success">↻ Hot</Badge>}
    >
      <div
        role="radiogroup"
        aria-label="테마 선택"
        className="grid grid-cols-1 gap-3 sm:grid-cols-3"
      >
        {OPTIONS.map(({ value, label, hint, Icon }) => {
          const selected = mode === value;
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => setMode(value)}
              className={cn(
                "flex flex-col items-start gap-2 rounded-[--radius-m] border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[--ring]",
                selected
                  ? "border-[--primary] bg-[--card]"
                  : "border-[--border-divider] bg-[--surface] hover:bg-[--card]",
              )}
            >
              <span className="flex items-center gap-2">
                <Icon size={16} aria-hidden className="text-[--primary]" />
                <span className="text-sm font-medium text-[--foreground-strong]">
                  {label}
                </span>
              </span>
              <span className="text-xs text-[--muted-foreground]">{hint}</span>
            </button>
          );
        })}
      </div>
      <p className="text-xs text-[--muted-foreground]">
        현재 적용 모드:{" "}
        <span className="font-medium text-[--foreground]">{resolved}</span>
        {mode === "system" ? " (시스템 추종)" : ""}
      </p>
    </SettingCard>
  );
}
