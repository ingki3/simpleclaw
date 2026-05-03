"use client";

/**
 * Tabs — DESIGN.md §3.1 Atomic / §5 a11y(role=tablist + aria-selected).
 *
 * 컨트롤된 단순 탭. URL 상태와 묶고 싶다면 부모에서 `value`/`onValueChange`로 라우팅을
 * 연결한다. 탭 전환은 fast motion으로 즉시 표시(panel은 부모가 관리).
 */

import { cn } from "@/lib/cn";

export interface TabItem<T extends string = string> {
  value: T;
  label: string;
  /** 우측 카운트 배지 — 표시 안 하려면 생략. */
  count?: number;
}

export interface TabsProps<T extends string = string> {
  items: readonly TabItem<T>[];
  value: T;
  onValueChange: (next: T) => void;
  className?: string;
  /** 탭 라벨용 보조 설명 — aria-label로 노출. */
  ariaLabel?: string;
}

export function Tabs<T extends string = string>({
  items,
  value,
  onValueChange,
  className,
  ariaLabel,
}: TabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-1 rounded-(--radius-m) border border-(--border) bg-(--card) p-1",
        className,
      )}
    >
      {items.map((item) => {
        const selected = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onValueChange(item.value)}
            className={cn(
              "inline-flex items-center gap-2 rounded-(--radius-sm) px-3 py-1.5 text-xs font-medium transition-colors",
              selected
                ? "bg-(--primary) text-(--primary-foreground)"
                : "text-(--muted-foreground) hover:bg-(--surface)",
            )}
          >
            <span>{item.label}</span>
            {typeof item.count === "number" ? (
              <span
                className={cn(
                  "rounded-(--radius-pill) px-1.5 py-0.5 text-[10px]",
                  selected
                    ? "bg-(--primary-foreground)/15 text-(--primary-foreground)"
                    : "bg-(--surface) text-(--muted-foreground)",
                )}
              >
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
