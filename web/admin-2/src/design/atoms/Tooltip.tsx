"use client";

/**
 * Tooltip — Atomic. 8s 안에 떠야 하는 마이크로 카피 (DESIGN.md §3.1).
 *
 * 구현 정책:
 *  - portal/popper 라이브러리 도입 없이 CSS 만으로 구현 — Admin 2.0 의 의존성 표면 최소화.
 *  - 트리거에 hover 또는 focus-visible 시 표시. 모바일/터치는 long-press 미지원 (TODO: BIZ-S2).
 *  - children 은 단일 element 여야 하며, 그대로 wrapping 한다.
 */

import { cloneElement, isValidElement, useState, type ReactElement, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export type TooltipSide = "top" | "bottom" | "left" | "right";

export interface TooltipProps {
  content: ReactNode;
  side?: TooltipSide;
  children: ReactElement;
}

const SIDE_POS: Record<TooltipSide, string> = {
  top: "bottom-full left-1/2 -translate-x-1/2 mb-2",
  bottom: "top-full left-1/2 -translate-x-1/2 mt-2",
  left: "right-full top-1/2 -translate-y-1/2 mr-2",
  right: "left-full top-1/2 -translate-y-1/2 ml-2",
};

export function Tooltip({ content, side = "top", children }: TooltipProps) {
  const [open, setOpen] = useState(false);
  if (!isValidElement(children)) return children as ReactNode;

  // 트리거의 기존 핸들러를 보존하면서 hover/focus 핸들러를 추가.
  const triggerProps = (children as ReactElement<Record<string, unknown>>).props;
  const cloned = cloneElement(
    children as ReactElement<Record<string, unknown>>,
    {
      onMouseEnter: (e: unknown) => {
        setOpen(true);
        const handler = triggerProps.onMouseEnter;
        if (typeof handler === "function") (handler as (...args: unknown[]) => void)(e);
      },
      onMouseLeave: (e: unknown) => {
        setOpen(false);
        const handler = triggerProps.onMouseLeave;
        if (typeof handler === "function") (handler as (...args: unknown[]) => void)(e);
      },
      onFocus: (e: unknown) => {
        setOpen(true);
        const handler = triggerProps.onFocus;
        if (typeof handler === "function") (handler as (...args: unknown[]) => void)(e);
      },
      onBlur: (e: unknown) => {
        setOpen(false);
        const handler = triggerProps.onBlur;
        if (typeof handler === "function") (handler as (...args: unknown[]) => void)(e);
      },
    },
  );

  return (
    <span className="relative inline-flex">
      {cloned}
      <span
        role="tooltip"
        aria-hidden={!open}
        className={cn(
          "pointer-events-none absolute z-10 whitespace-nowrap rounded-(--radius-sm) bg-(--foreground-strong) px-2 py-1 text-xs text-(--background) shadow-(--shadow-m) transition-opacity",
          SIDE_POS[side],
          open ? "opacity-100" : "opacity-0",
        )}
      >
        {content}
      </span>
    </span>
  );
}
