"use client";

/**
 * Toast — DESIGN.md §3.3 / §1 #6 Reversibility.
 *
 * 모든 mutation 성공/실패가 본 토스트 viewport에 누적된다. 4가지 톤:
 *   success / info / warn / destructive-soft (위험 작업의 *되돌릴 수 있는* 결과)
 *
 * 추가 기능:
 *   - ``undo`` 액션 슬롯 — 5분 윈도우 안에서 ``useUndo``와 결합해 마지막 변경을 취소.
 *   - 토스트는 기본 5초 후 자동 dismiss(undo 슬롯이 있으면 윈도우만큼 유지).
 *   - 다중 토스트는 우하단 stack(z 50). 키보드 포커스는 빼앗지 않는다.
 *
 * 사용:
 *   <ToastProvider>
 *     <App />
 *   </ToastProvider>
 *   ...
 *   const { push } = useToast();
 *   push({ tone: 'success', title: '적용됐어요.', undo: { onUndo, expiresAt } });
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
import { CheckCircle2, Info, AlertTriangle, X, Undo2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { onAdminApiError } from "@/lib/api/client";
import type { AdminApiError } from "@/lib/api/errors";

export type ToastTone = "success" | "info" | "warn" | "destructive-soft";

export interface ToastUndoAction {
  /** undo 클릭 시 호출되는 핸들러. 비동기 가능. */
  onUndo: () => void | Promise<void>;
  /** 윈도우 만료 시각(epoch ms). 토스트는 이 시각까지 표시된다. */
  expiresAt: number;
  /** 카운트다운 라벨에 표시할 변경 이름 — 미지정 시 "되돌리기". */
  label?: string;
}

export interface ToastInput {
  tone?: ToastTone;
  title: ReactNode;
  description?: ReactNode;
  /** 자동 닫힘까지의 ms — undo가 있으면 이 값은 무시되고 expiresAt까지 유지. */
  durationMs?: number;
  undo?: ToastUndoAction;
}

interface ToastItem extends ToastInput {
  id: string;
  createdAt: number;
}

interface ToastContextShape {
  push: (input: ToastInput) => string;
  dismiss: (id: string) => void;
  toasts: ReadonlyArray<ToastItem>;
}

const ToastContext = createContext<ToastContextShape | null>(null);

const TONE_STYLE: Record<ToastTone, { icon: typeof Info; cls: string }> = {
  success: {
    icon: CheckCircle2,
    cls: "border-(--color-success) bg-(--color-success-bg)",
  },
  info: {
    icon: Info,
    cls: "border-(--color-info) bg-(--color-info-bg)",
  },
  warn: {
    icon: AlertTriangle,
    cls: "border-(--color-warning) bg-(--color-warning-bg)",
  },
  "destructive-soft": {
    icon: AlertTriangle,
    cls: "border-(--color-error) bg-(--color-error-bg)",
  },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  // 마지막 push id 카운터 — 키 안정성을 위해 단순 카운터 사용.
  const counterRef = useRef(0);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (input: ToastInput): string => {
      counterRef.current += 1;
      const id = `t${counterRef.current}-${Date.now().toString(36)}`;
      const item: ToastItem = {
        ...input,
        id,
        tone: input.tone ?? "info",
        createdAt: Date.now(),
      };
      setToasts((prev) => [...prev, item]);

      // 자동 dismiss — undo 윈도우는 별도 효과에서 처리.
      if (!item.undo) {
        const dur = item.durationMs ?? 5000;
        window.setTimeout(() => dismiss(id), dur);
      }
      return id;
    },
    [dismiss],
  );

  // AdminApiError가 던져질 때 자동으로 토스트로 라우팅 — DoD: 401/403/5xx 일관 처리.
  useEffect(() => {
    return onAdminApiError((err) => {
      const tone: ToastTone =
        err.kind === "unauthorized" || err.kind === "server"
          ? "destructive-soft"
          : "warn";
      push({
        tone,
        title: _toastTitleForError(err),
        description: err.message,
      });
    });
  }, [push]);

  const value = useMemo<ToastContextShape>(
    () => ({ push, dismiss, toasts }),
    [push, dismiss, toasts],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function _toastTitleForError(err: AdminApiError): string {
  switch (err.kind) {
    case "unauthorized":
      return "인증 실패";
    case "forbidden":
      return "권한 없음";
    case "validation":
      return "입력 오류";
    case "server":
      return "데몬 오류";
    case "network":
      return "연결 실패";
    case "not_found":
      return "찾을 수 없음";
    case "conflict":
      return "충돌";
    default:
      return "알 수 없는 오류";
  }
}

export function useToast(): ToastContextShape {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast는 ToastProvider 내부에서만 사용할 수 있어요.");
  }
  return ctx;
}

interface ViewportProps {
  toasts: ReadonlyArray<ToastItem>;
  onDismiss: (id: string) => void;
}

function ToastViewport({ toasts, onDismiss }: ViewportProps) {
  return (
    <div
      // 토스트는 키보드 포커스를 빼앗지 않도록 ``polite`` live region.
      role="region"
      aria-label="알림"
      className="pointer-events-none fixed bottom-6 right-6 z-50 flex w-full max-w-sm flex-col gap-2"
    >
      {toasts.map((t) => (
        <ToastCard key={t.id} item={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

interface CardProps {
  item: ToastItem;
  onDismiss: () => void;
}

function ToastCard({ item, onDismiss }: CardProps) {
  const tone = TONE_STYLE[item.tone ?? "info"];
  const Icon = tone.icon;
  const [now, setNow] = useState(Date.now());
  const [undoing, setUndoing] = useState(false);

  // undo가 있으면 1초 단위 카운트다운 + 만료 시 자동 dismiss.
  useEffect(() => {
    if (!item.undo) return;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    const remain = item.undo.expiresAt - Date.now();
    const timer = window.setTimeout(onDismiss, Math.max(0, remain));
    return () => {
      window.clearInterval(tick);
      window.clearTimeout(timer);
    };
  }, [item.undo, onDismiss]);

  const remainingS = item.undo
    ? Math.max(0, Math.ceil((item.undo.expiresAt - now) / 1000))
    : 0;

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "pointer-events-auto flex items-start gap-3 rounded-(--radius-m) border bg-(--card-elevated) px-4 py-3 text-sm text-(--foreground) shadow-(--shadow-l)",
        tone.cls,
      )}
    >
      <Icon size={16} aria-hidden className="mt-0.5 shrink-0" />
      <div className="flex-1">
        <div className="font-medium text-(--foreground-strong)">{item.title}</div>
        {item.description && (
          <div className="mt-1 text-xs text-(--muted-foreground)">
            {item.description}
          </div>
        )}
        {item.undo && (
          <div className="mt-2 flex items-center gap-3">
            <button
              type="button"
              disabled={undoing}
              onClick={async () => {
                setUndoing(true);
                try {
                  await item.undo?.onUndo();
                  onDismiss();
                } finally {
                  setUndoing(false);
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--border-strong) bg-(--card) px-2.5 py-1 text-xs font-medium text-(--foreground) hover:bg-(--surface) disabled:opacity-50"
            >
              <Undo2 size={12} aria-hidden />
              <span>{item.undo.label ?? "되돌리기"}</span>
            </button>
            <span
              className="font-mono text-[10px] text-(--muted-foreground)"
              aria-label={`${remainingS}초 남음`}
            >
              {_fmtMmSs(remainingS)} 남음
            </span>
          </div>
        )}
      </div>
      <button
        type="button"
        aria-label="알림 닫기"
        onClick={onDismiss}
        className="-mr-1 -mt-1 grid h-6 w-6 place-items-center rounded-(--radius-sm) text-(--muted-foreground) hover:bg-(--surface) hover:text-(--foreground)"
      >
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}

function _fmtMmSs(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}
