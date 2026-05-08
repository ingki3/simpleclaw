"use client";

/**
 * Modal — Channels 영역 (Token Rotate / Webhook Edit / Traffic Simulation) 공유 wrapper.
 *
 * skills-recipes / llm-router 의 Modal 과 동일한 시각 spec — admin.pen 의 라운드 카드 +
 * 검은색 50% 백드롭 + 우상단 X · 하단 액션 행. 영역 간 경량 중복을 허용하되, 시각이 어긋나지
 * 않도록 토큰만 사용하도록 강제한다.
 *
 * ESC/백드롭 클릭으로 닫히고, 내부 클릭은 stopPropagation. focus trap 은
 * 본 단계에서 input autofocus 로만 처리한다.
 */

import { useEffect, type ReactNode } from "react";
import { cn } from "@/lib/cn";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** Dialog 내부 헤더 — 보통 `<h2>` + 보조 메타. */
  title: ReactNode;
  /** 우하단 액션 행 — 취소/저장 등의 Button 들. */
  footer: ReactNode;
  /** 좌하단 부가 액션 (예: "트래픽 시뮬레이션" 트리거). */
  footerLeft?: ReactNode;
  /** 카드 폭 — sm(420), md(560), lg(800). */
  width?: "sm" | "md" | "lg";
  /** 테스트/E2E 용 식별자. */
  "data-testid"?: string;
  children: ReactNode;
}

const WIDTH: Record<NonNullable<ModalProps["width"]>, string> = {
  sm: "max-w-md",
  md: "max-w-xl",
  lg: "max-w-3xl",
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
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[10vh]"
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

        <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto px-6 pb-6">
          {children}
        </div>

        <footer className="flex items-center justify-between gap-2 border-t border-(--border) bg-(--card) px-6 py-3">
          <div>{footerLeft}</div>
          <div className="flex items-center gap-2">{footer}</div>
        </footer>
      </div>
    </div>
  );
}
