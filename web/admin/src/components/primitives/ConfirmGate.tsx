"use client";

/**
 * ConfirmGate — DESIGN.md §3.2 / §4.2 ("텍스트 confirm 입력 + 카운트다운 게이지").
 *
 * 파괴적 액션 직전에 띄우는 모달. 운영자가 정확한 확인 문자열을 입력해야만
 * Confirm 버튼이 활성화되고, 그 외에는 도주(Escape/취소) 외 경로가 없다.
 *
 * BIZ-46에서는 MEMORY.md 영구 삭제에 사용된다 — 입력 일치(`MEMORY.md` 같은
 * 식별 문자열)로 의도를 한 번 더 검증한다.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { cn } from "@/lib/cn";

export interface ConfirmGateProps {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  /** 운영자가 그대로 입력해야 통과되는 문자열 (예: 파일명). */
  expectedInput: string;
  /** 입력란 placeholder — 보통 `expectedInput과 동일하게 입력` 같은 안내. */
  inputLabel?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function ConfirmGate({
  open,
  title,
  description,
  expectedInput,
  inputLabel,
  confirmLabel = "삭제",
  cancelLabel = "취소",
  onConfirm,
  onCancel,
}: ConfirmGateProps) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 모달이 열릴 때마다 입력값을 초기화하고 입력란에 포커스
  useEffect(() => {
    if (open) {
      setValue("");
      setBusy(false);
      // 다음 tick에 포커스 (DOM mount 이후)
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Escape로 닫기 — 작업 중에는 잠금
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const matches = value.trim() === expectedInput;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-gate-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      // 배경 클릭으로 닫기 (busy일 때는 잠금)
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        ref={dialogRef}
        className={cn(
          "w-full max-w-md rounded-[--radius-l] border border-[--border] bg-[--card] p-6 shadow-[--shadow-l]",
        )}
      >
        <div className="flex items-start gap-3">
          <span className="mt-0.5 shrink-0">
            <AlertTriangle
              size={20}
              className="text-[--color-error]"
              aria-hidden
            />
          </span>
          <div className="min-w-0 flex-1">
            <h2
              id="confirm-gate-title"
              className="text-base font-semibold text-[--foreground-strong]"
            >
              {title}
            </h2>
            {description ? (
              <div className="mt-1 text-sm text-[--muted-foreground]">
                {description}
              </div>
            ) : null}
          </div>
        </div>

        <div className="mt-4">
          <label
            htmlFor="confirm-gate-input"
            className="mb-1 block text-xs text-[--muted-foreground]"
          >
            {inputLabel ?? `확인을 위해 "${expectedInput}"를 입력하세요`}
          </label>
          <Input
            id="confirm-gate-input"
            ref={inputRef}
            value={value}
            invalid={value.length > 0 && !matches}
            onChange={(e) => setValue(e.target.value)}
            placeholder={expectedInput}
            disabled={busy}
            autoComplete="off"
          />
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            size="md"
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </Button>
          <Button
            variant="destructive"
            size="md"
            disabled={!matches || busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
