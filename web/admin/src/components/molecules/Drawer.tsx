"use client";

/**
 * Drawer — 우측 슬라이드 패널.
 *
 * DESIGN.md §3.2 Molecular(원형 정의는 없으나 §4.1 Setting Edit Pattern과 정합).
 * 본 1차 사용처는 BIZ-47 Skills 화면의 스킬 상세 표시. 모달과 달리 백그라운드 화면을
 * 가리지 않고 우측 영역에 정주(width 480px)하며, ESC와 외부 클릭으로 닫힌다.
 *
 * 접근성:
 *  - role=dialog + aria-modal=true(포커스 트랩은 후속 — 1차는 ESC/Close만 보장)
 *  - 열릴 때 Drawer 컨테이너에 포커스 이동, 닫힐 때 트리거에 복귀(opener 책임).
 */

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** 부제 — 보통 ID 또는 경로. mono 폰트로 노출. */
  subtitle?: string;
  /** 우측 헤더 슬롯 — 토글, 추가 액션. */
  headerRight?: ReactNode;
  children: ReactNode;
  /** sticky footer — 보통 액션 버튼. */
  footer?: ReactNode;
  className?: string;
}

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  headerRight,
  children,
  footer,
  className,
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  // ESC로 닫기 — 키보드 사용자 보장.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // 열릴 때 패널 자체로 포커스 이동 — 스크린리더가 새 컨텍스트를 인식하도록.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex">
      {/* 백드롭 — 외부 클릭으로 닫기. 반투명도는 design token 미정의이므로 inline. */}
      <button
        type="button"
        aria-label="패널 닫기"
        onClick={onClose}
        className="flex-1 bg-black/30 transition-opacity"
        style={{ animation: "fade-in var(--motion-base)" }}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "flex h-full w-[480px] max-w-[100vw] flex-col bg-(--background) shadow-(--shadow-l) outline-none",
          className,
        )}
        style={{ animation: "drawer-slide-in var(--motion-base)" }}
      >
        <header className="flex items-start justify-between gap-3 border-b border-(--border) px-6 py-4">
          <div className="flex min-w-0 flex-col gap-1">
            <h2 className="truncate text-lg font-semibold text-(--foreground-strong)">
              {title}
            </h2>
            {subtitle ? (
              <code className="truncate font-mono text-xs text-(--muted-foreground)">
                {subtitle}
              </code>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {headerRight}
            <button
              type="button"
              aria-label="닫기"
              onClick={onClose}
              className="grid h-8 w-8 place-items-center rounded-(--radius-m) text-(--muted-foreground) transition-colors hover:bg-(--surface) hover:text-(--foreground)"
            >
              <X size={16} aria-hidden />
            </button>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-(--border) bg-(--surface) px-6 py-3">
            {footer}
          </footer>
        ) : null}
      </div>

      {/* keyframe — globals에 두지 않은 이유: drawer 전용이라 응집도 측면. */}
      <style>{`
        @keyframes drawer-slide-in { from { transform: translateX(100%); } to { transform: translateX(0); } }
        @keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
      `}</style>
    </div>
  );
}
