"use client";

/**
 * Drawer — 우측에서 슬라이드되는 보조 패널 (DESIGN.md §3.3).
 *
 * Modal과 동일한 시맨틱(role="dialog")을 갖되, 폭 360–520 / 우측 fix 위치로
 * 1차 액션을 가리지 않고 보조 정보를 띄우는 데 쓴다 — 예: 시크릿 메타,
 * 트레이스 상세, 변경 이력.
 *
 * 디자인:
 *  - 모션은 ``motion-base`` slide-in. ``prefers-reduced-motion``이면 fade만.
 *  - 본문은 `min-h: 100vh` — 세로로 스크롤 가능, 헤더는 sticky.
 */

import {
  useEffect,
  useId,
  useRef,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export type DrawerSize = "sm" | "md" | "lg";

export interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  /** 폭 — sm:360 / md:480 / lg:560. */
  size?: DrawerSize;
  className?: string;
  children?: ReactNode;
}

const WIDTH: Record<DrawerSize, string> = {
  sm: "w-[360px]",
  md: "w-[480px]",
  lg: "w-[560px]",
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Drawer({
  open,
  onOpenChange,
  title,
  description,
  footer,
  size = "md",
  className,
  children,
}: DrawerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = window.setTimeout(() => {
      const root = containerRef.current;
      if (!root) return;
      const focusable = root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      (focusable[0] ?? root).focus();
    }, 0);
    return () => {
      window.clearTimeout(t);
      document.body.style.overflow = prevOverflow;
      if (previousFocusRef.current?.isConnected) {
        previousFocusRef.current.focus();
      }
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onOpenChange(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descId : undefined}
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={() => onOpenChange(false)}
    >
      <div
        ref={containerRef}
        onClick={(e) => e.stopPropagation()}
        tabIndex={-1}
        className={cn(
          "flex h-full max-w-full flex-col border-l border-(--border) bg-(--card-elevated) shadow-(--shadow-l) outline-none motion-safe:animate-[drawer-in_180ms_cubic-bezier(.2,.8,.2,1)]",
          WIDTH[size],
          className,
        )}
      >
        <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-(--border) bg-(--card-elevated) px-5 py-4">
          <div className="flex-1">
            {title && (
              <h2
                id={titleId}
                className="text-md font-semibold text-(--foreground-strong)"
              >
                {title}
              </h2>
            )}
            {description && (
              <p
                id={descId}
                className="mt-1 text-sm text-(--muted-foreground)"
              >
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            aria-label="닫기"
            onClick={() => onOpenChange(false)}
            className="-mr-1 grid h-8 w-8 place-items-center rounded-(--radius-m) text-(--muted-foreground) hover:bg-(--surface) hover:text-(--foreground)"
          >
            <X size={16} aria-hidden />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm text-(--foreground)">
          {children}
        </div>
        {footer && (
          <footer className="sticky bottom-0 flex items-center justify-end gap-2 border-t border-(--border) bg-(--card-elevated) px-5 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
