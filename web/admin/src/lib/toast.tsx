"use client";

/**
 * Toast 시스템 — DESIGN.md §4.1(Toast(undo)) / §6.3(마이크로카피).
 *
 * 한 화면에 토스트 영역은 하나만 둔다(우하단 stack). 컴포넌트는 ``useToast()``로
 * trigger만 호출하면 되고, 표시 위치·애니메이션·자동 폐기는 본 모듈이 책임진다.
 *
 * 디자인 결정:
 *  - tone은 success/error/info 셋만 — warning은 RestartBanner 같은 inline UI로 흡수.
 *  - 자동 폐기 4s, undo 콜백을 받는 토스트는 6s로 살짝 더 머문다(취소 의사 시간 확보).
 *  - aria-live=polite로 스크린리더 알림(에러는 assertive로 격상).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, AlertCircle, Info, X, Undo2 } from "lucide-react";
import { cn } from "@/lib/cn";

export type ToastTone = "success" | "error" | "info";

export interface ToastOptions {
  tone?: ToastTone;
  title: string;
  /** 보조 본문 — 한 줄 정도. */
  description?: string;
  /** 사용자가 명시적으로 닫기 전까지 유지. undo와 결합해 쓰기 좋다. */
  sticky?: boolean;
  /** undo 액션 — 클릭하면 onUndo 호출 후 즉시 폐기. */
  onUndo?: () => void;
}

interface ToastEntry extends ToastOptions {
  id: number;
}

interface ToastContextValue {
  /** 토스트 1개 띄우고 id를 반환한다 — 호출자는 id로 수동 dismiss 가능. */
  push(opts: ToastOptions): number;
  dismiss(id: number): void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 4_000;
const AUTO_DISMISS_WITH_UNDO_MS = 6_000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  // 동시 트리거 시 id 충돌을 막기 위해 monotonic counter를 ref로 둔다.
  const seq = useRef(0);
  // setTimeout 핸들 — unmount 시 정리.
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const handle = timers.current.get(id);
    if (handle) {
      clearTimeout(handle);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (opts: ToastOptions) => {
      const id = ++seq.current;
      const entry: ToastEntry = { tone: "success", ...opts, id };
      setToasts((prev) => [...prev, entry]);
      if (!opts.sticky) {
        const ttl = opts.onUndo ? AUTO_DISMISS_WITH_UNDO_MS : AUTO_DISMISS_MS;
        const handle = setTimeout(() => dismiss(id), ttl);
        timers.current.set(id, handle);
      }
      return id;
    },
    [dismiss],
  );

  // unmount 시 모든 타이머 해제 — 메모리 leak / late update 방지.
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach(clearTimeout);
      map.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({ push, dismiss }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

// ---------------------------------------------------------------------------
// Viewport
// ---------------------------------------------------------------------------

const ICON: Record<ToastTone, typeof CheckCircle2> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};

const TONE_CLASS: Record<ToastTone, string> = {
  success: "border-[--color-success] bg-[--color-success-bg] text-[--color-success]",
  error: "border-[--color-error] bg-[--color-error-bg] text-[--color-error]",
  info: "border-[--color-info] bg-[--color-info-bg] text-[--color-info]",
};

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastEntry[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      aria-label="알림"
      className="pointer-events-none fixed bottom-6 right-6 z-50 flex w-[360px] max-w-[calc(100vw-32px)] flex-col gap-2"
    >
      {toasts.map((t) => {
        const tone = t.tone ?? "success";
        const Icon = ICON[tone];
        return (
          <div
            key={t.id}
            role="status"
            aria-live={tone === "error" ? "assertive" : "polite"}
            className="pointer-events-auto flex items-start gap-3 rounded-[--radius-m] border border-[--border] bg-[--card] p-3 shadow-[--shadow-m]"
          >
            <span
              aria-hidden
              className={cn(
                "grid h-7 w-7 shrink-0 place-items-center rounded-[--radius-pill] border",
                TONE_CLASS[tone],
              )}
            >
              <Icon size={14} />
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <p className="text-sm font-medium text-[--foreground-strong]">
                {t.title}
              </p>
              {t.description ? (
                <p className="text-xs text-[--muted-foreground]">
                  {t.description}
                </p>
              ) : null}
              {t.onUndo ? (
                <button
                  type="button"
                  onClick={() => {
                    t.onUndo?.();
                    onDismiss(t.id);
                  }}
                  className="mt-1 inline-flex w-fit items-center gap-1 rounded-[--radius-sm] text-xs font-medium text-[--primary] hover:underline"
                >
                  <Undo2 size={12} aria-hidden />
                  되돌리기
                </button>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="알림 닫기"
              onClick={() => onDismiss(t.id)}
              className="text-[--muted-foreground] transition-colors hover:text-[--foreground]"
            >
              <X size={14} aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
