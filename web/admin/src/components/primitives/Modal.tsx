"use client";

/**
 * Modal — DESIGN.md §3.3 Layout / §5 Accessibility.
 *
 * 책임:
 *  - 오버레이 + role="dialog"/aria-modal로 시맨틱 모달.
 *  - Escape, 바깥 클릭으로 닫기. 진입 시 첫 포커스 가능 요소로 포커스 이동.
 *  - 단순한 focus trap — Tab/Shift+Tab을 다이얼로그 내부에 가둔다.
 *  - 위험 등급에서는 ``role="alertdialog"``로 옵트인 가능.
 *
 * 비책임:
 *  - 내부 컨텐츠 레이아웃은 호출자가 결정한다(헤더/풋터 슬롯 제공).
 *  - 토스트/Drawer와 z-index 계층은 globals.css에서 관리.
 *
 * 디자인 결정:
 *  - 포털을 강제하지 않는다 — 1차 SimpleClaw Admin은 단일 트리이므로 Shell 안에서
 *    렌더되어도 z-index 50으로 충분히 띄울 수 있다. 후속에서 portal이 필요하면
 *    ``portalContainer`` prop을 추가할 것.
 */

import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export type ModalSize = "sm" | "md" | "lg";

export interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 다이얼로그 헤더 — 보통 짧은 문장 1줄. 미지정 시 헤더 영역 자체가 생략. */
  title?: ReactNode;
  /** 보조 설명 — 헤더 아래에 작은 글씨. */
  description?: ReactNode;
  /** 푸터 슬롯 — 일반적으로 Cancel/Apply 버튼 묶음. */
  footer?: ReactNode;
  /** 모달 너비 — sm:480 / md:560 / lg:720 (DESIGN.md 기본 그리드와 정합). */
  size?: ModalSize;
  /** 위험 confirm 모달이면 ``alertdialog``로 시맨틱 격상. */
  alert?: boolean;
  /** 바깥 클릭/ESC 닫기 비활성화 — 진행 중 작업에서 사용. */
  dismissible?: boolean;
  className?: string;
  children?: ReactNode;
}

const SIZE: Record<ModalSize, string> = {
  sm: "max-w-[480px]",
  md: "max-w-[560px]",
  lg: "max-w-[720px]",
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  footer,
  size = "md",
  alert = false,
  dismissible = true,
  className,
  children,
}: ModalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  // 진입 시: body 스크롤 잠금, 이전 포커스 보관, 첫 포커스 이동.
  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // 다음 틱에 포커스 — 컨텐츠 마운트 후.
    const t = window.setTimeout(() => {
      const root = containerRef.current;
      if (!root) return;
      const focusable = root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusable.length > 0) {
        focusable[0].focus();
      } else {
        root.focus();
      }
    }, 0);

    return () => {
      window.clearTimeout(t);
      document.body.style.overflow = prevOverflow;
      // 포커스 복원 — 이전 element가 여전히 DOM에 있으면.
      if (previousFocusRef.current?.isConnected) {
        previousFocusRef.current.focus();
      }
    };
  }, [open]);

  // Escape 닫기.
  useEffect(() => {
    if (!open || !dismissible) return;
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onOpenChange(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, dismissible, onOpenChange]);

  // 단순한 focus trap — Tab/Shift+Tab만 케어.
  function handleKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Tab") return;
    const root = containerRef.current;
    if (!root) return;
    const focusable = Array.from(
      root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
    ).filter((el) => !el.hasAttribute("data-focus-skip"));
    if (focusable.length === 0) {
      e.preventDefault();
      root.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (e.shiftKey) {
      if (active === first || !root.contains(active)) {
        e.preventDefault();
        last.focus();
      }
    } else if (active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  if (!open) return null;
  return (
    <div
      role={alert ? "alertdialog" : "dialog"}
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descriptionId : undefined}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={() => {
        if (dismissible) onOpenChange(false);
      }}
    >
      <div
        ref={containerRef}
        // 본문 컨테이너: 바깥 클릭 닫기 막고, focus trap을 동작시킨다.
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
        tabIndex={-1}
        className={cn(
          "relative flex w-full flex-col rounded-[--radius-l] border border-[--border] bg-[--card-elevated] shadow-[--shadow-l] outline-none",
          SIZE[size],
          className,
        )}
      >
        {(title || dismissible) && (
          <header className="flex items-start gap-3 border-b border-[--border] px-6 py-4">
            <div className="flex-1">
              {title && (
                <h2
                  id={titleId}
                  className="text-md font-semibold text-[--foreground-strong]"
                >
                  {title}
                </h2>
              )}
              {description && (
                <p
                  id={descriptionId}
                  className="mt-1 text-sm text-[--muted-foreground]"
                >
                  {description}
                </p>
              )}
            </div>
            {dismissible && (
              <button
                type="button"
                aria-label="닫기"
                onClick={() => onOpenChange(false)}
                className="-mr-1 grid h-8 w-8 place-items-center rounded-[--radius-m] text-[--muted-foreground] hover:bg-[--surface] hover:text-[--foreground]"
              >
                <X size={16} aria-hidden />
              </button>
            )}
          </header>
        )}
        <div className="flex-1 px-6 py-5 text-sm text-[--foreground]">
          {children}
        </div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-[--border] px-6 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
