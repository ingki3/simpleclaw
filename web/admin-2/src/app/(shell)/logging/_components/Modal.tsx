"use client";

/**
 * Modal — Logging & Traces 영역 dialog wrapper.
 *
 * llm-router / skills-recipes 의 Modal 과 동일한 패턴이지만, Trace Detail 의
 * 큰 본문(스팬 인스펙터 + Raw JSON) 을 담기 위해 width 옵션을 `xl` 까지 확장한다.
 *
 * Shell 의 `CommandPalette` 와 같이 ESC/백드롭 클릭으로 닫히고, 내부 클릭은 stopPropagation.
 */

import { useEffect, type ReactNode } from "react";
import { cn } from "@/lib/cn";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** Dialog 내부 헤더 — 보통 `<h2>` + 보조 메타. */
  title: ReactNode;
  /** 우하단 액션 행 — 보통 닫기 버튼만. */
  footer?: ReactNode;
  /** 좌하단 부가 액션 (옵션). */
  footerLeft?: ReactNode;
  /** 카드 폭 — Trace Detail 은 xl 사용. */
  width?: "sm" | "md" | "lg" | "xl";
  /** 테스트/E2E 용 식별자. */
  "data-testid"?: string;
  children: ReactNode;
}

const WIDTH: Record<NonNullable<ModalProps["width"]>, string> = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

export function Modal({
  open,
  onClose,
  title,
  footer,
  footerLeft,
  width = "md",
  children,
  ...rest
}: ModalProps) {
  // ESC 로 닫기 — Shell 의 CommandPalette 가 동일 패턴.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid={rest["data-testid"]}
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[8vh]"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "w-full overflow-hidden rounded-(--radius-l) border border-(--border) bg-(--card-elevated) shadow-[var(--shadow-l)]",
          WIDTH[width],
        )}
      >
        <header className="flex items-start justify-between gap-3 px-6 py-4">
          <div className="min-w-0 flex-1">{title}</div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            data-testid="modal-close"
            className="-mr-2 -mt-1 inline-flex h-8 w-8 items-center justify-center rounded-(--radius-m) text-(--muted-foreground) hover:bg-(--surface) hover:text-(--foreground)"
          >
            ×
          </button>
        </header>

        <div className="flex max-h-[75vh] flex-col gap-4 overflow-y-auto px-6 pb-6">
          {children}
        </div>

        {footer || footerLeft ? (
          <footer className="flex items-center justify-between gap-2 border-t border-(--border) bg-(--card) px-6 py-3">
            <div>{footerLeft}</div>
            <div className="flex items-center gap-2">{footer}</div>
          </footer>
        ) : null}
      </div>
    </div>
  );
}
