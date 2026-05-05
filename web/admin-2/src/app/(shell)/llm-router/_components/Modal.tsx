"use client";

/**
 * Modal — LLM Router 의 3개 sub-flow 모달이 공유하는 dialog wrapper.
 *
 * admin.pen `oUzPN` (Add Provider) · `AzGck` (Edit Provider) · `Sms7l` (Routing Rule Editor)
 * 의 공통 시각: 라운드 카드 + 검은색 50% 백드롭 + 우상단 X · 우하단 액션 행.
 *
 * Shell 의 `CommandPalette` 와 같이 ESC/백드롭 클릭으로 닫히고, 내부 클릭은 stopPropagation.
 * 본 단계에서는 focus trap 은 별도 라이브러리 없이 input autofocus 로만 처리한다.
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
  /** 좌하단 부가 액션 — 예: "프로바이더 삭제" 같은 destructive 보조 액션. */
  footerLeft?: ReactNode;
  /** 카드 폭 — admin.pen 모달들이 520px / 720px 두 종류라 max-w 토큰화. */
  width?: "sm" | "md" | "lg";
  /** 테스트/E2E 용 식별자. */
  "data-testid"?: string;
  children: ReactNode;
}

const WIDTH: Record<NonNullable<ModalProps["width"]>, string> = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
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
