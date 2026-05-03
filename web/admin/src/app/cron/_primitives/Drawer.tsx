"use client";

/**
 * Drawer — 우측에서 슬라이드 인.
 *
 * Cron 화면에서 잡 행을 클릭했을 때 실행 이력을 옆 패널로 띄우는 용도.
 * Modal과 동일하게 BIZ-43가 정식 primitive를 제공할 예정.
 */

import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/atoms/Button";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
}

export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
}: DrawerProps) {
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
      className="fixed inset-0 z-40 flex justify-end"
    >
      <button
        type="button"
        aria-label="닫기"
        onClick={onClose}
        className="absolute inset-0 bg-black/30"
      />
      <aside className="relative z-10 flex h-full w-full max-w-lg flex-col border-l border-(--border) bg-(--card) shadow-(--shadow-l)">
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
      </aside>
    </div>
  );
}
