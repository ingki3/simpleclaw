"use client";

/**
 * 미니 Toast — Cron 화면 한정.
 *
 * 단일 활성 토스트만 표시한다. 5초 후 자동 dismiss + 명시적 close 버튼.
 * BIZ-43가 정식 ``Toast`` provider를 제공하면 ``useToast``의 시그니처는
 * 유지한 채 구현만 교체한다.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export type ToastTone = "success" | "info" | "warning" | "error";

interface ToastState {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastContextValue {
  show: (tone: ToastTone, message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TONE_CLASS: Record<ToastTone, string> = {
  success: "bg-(--color-success-bg) text-(--color-success)",
  info: "bg-(--color-info-bg) text-(--color-info)",
  warning: "bg-(--color-warning-bg) text-(--color-warning)",
  error: "bg-(--color-error-bg) text-(--color-error)",
};

/** 토스트가 자동 dismiss되기까지의 ms. 사용자 입력이 끊기는 시간을 짧게 유지. */
const AUTO_DISMISS_MS = 5_000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const seqRef = useRef(0);

  const show = useCallback((tone: ToastTone, message: string) => {
    seqRef.current += 1;
    setToast({ id: seqRef.current, tone, message });
  }, []);

  // 자동 dismiss — 같은 id가 떠 있는 동안만 유효.
  useEffect(() => {
    if (!toast) return;
    const targetId = toast.id;
    const timer = setTimeout(() => {
      setToast((cur) => (cur && cur.id === targetId ? null : cur));
    }, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {toast ? (
        <div
          role="status"
          aria-live="polite"
          className={cn(
            "fixed bottom-6 right-6 z-50 flex max-w-md items-start gap-3 rounded-(--radius-l) border border-(--border) px-4 py-3 shadow-(--shadow-m)",
            TONE_CLASS[toast.tone],
          )}
        >
          <span className="flex-1 text-sm">{toast.message}</span>
          <button
            type="button"
            aria-label="닫기"
            onClick={() => setToast(null)}
            className="text-current opacity-70 hover:opacity-100"
          >
            <X size={14} aria-hidden />
          </button>
        </div>
      ) : null}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast는 ToastProvider 안에서 호출되어야 해요.");
  }
  return ctx;
}
