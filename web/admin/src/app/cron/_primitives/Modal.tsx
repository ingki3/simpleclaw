"use client";

/**
 * Modal — Cron 화면 로컬 primitives.
 *
 * BIZ-43가 ``components/primitives/Modal``을 정식으로 들여올 예정이라,
 * 여기서는 다른 화면과 충돌하지 않도록 라우트 비공개 폴더(``_primitives``)에
 * 최소 구현만 둔다. BIZ-43가 머지되면 import 경로만 바꾸어 교체한다.
 *
 * 시각: 가운데 정렬, max-width 480~640, ESC 닫기, 백드롭 클릭 닫기,
 * 트랩(focus loop)은 단일 페이지 한정 사용이라 생략 — 한 번에 한 모달이
 * 떠 있다는 가정.
 */

import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { cn } from "@/lib/cn";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** sticky footer 슬롯 — 보통 Cancel + Apply 버튼 쌍. */
  footer?: ReactNode;
  /** 본문에서 폭을 늘리고 싶을 때 ``"wide"``. */
  size?: "default" | "wide";
  children: ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  size = "default",
  children,
}: ModalProps) {
  // ESC 닫기 — open 일 때만 리스너 활성화.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center"
    >
      {/* 백드롭 — 클릭 시 닫기. 포커스 누수를 막기 위해 본 div도 클릭 영역. */}
      <button
        type="button"
        aria-label="닫기"
        onClick={onClose}
        className="absolute inset-0 bg-black/40"
      />
      <div
        className={cn(
          "relative z-10 flex max-h-[85vh] w-full flex-col rounded-(--radius-l) border border-(--border) bg-(--card) shadow-(--shadow-l)",
          size === "wide" ? "max-w-2xl" : "max-w-md",
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-(--border) px-6 py-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold text-(--foreground-strong)">
              {title}
            </h2>
            {description ? (
              <p className="text-sm text-(--muted-foreground)">{description}</p>
            ) : null}
          </div>
          <Button
            variant="ghost"
            size="sm"
            aria-label="닫기"
            onClick={onClose}
          >
            <X size={16} aria-hidden />
          </Button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-(--border) bg-(--surface) px-6 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
