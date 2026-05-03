"use client";

/**
 * ConfirmGate — DESIGN.md §3.2 / §1 #5 위험 변경 격리.
 *
 * 파괴적 액션(시크릿 회전, 데몬 재시작, 삭제)에 대해 *텍스트 일치 입력*을 요구한다.
 * 운영자가 정확히 ``ROTATE`` 또는 ``DELETE``를 입력해야만 ``Confirm`` 버튼이 활성화.
 *
 * 본 컴포넌트는 :Modal 위에 얹은 wrapper다. ``alert: true`` 모달로 시맨틱 격상하며,
 * 입력값과 ``confirmation`` prop이 정확히 일치할 때만 ``onConfirm``이 호출된다.
 *
 * 디자인 결정:
 *  - 입력 비교는 *대소문자 구분* — 운영자가 진짜로 의도했는지 강제하기 위함.
 *  - 진행 중(isPending)에는 입력/취소도 비활성화 — 모달은 dismissible=false가 된다.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/atoms/Button";
import { Modal } from "./Modal";

export interface ConfirmGateProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 다이얼로그 헤더 — 예: "Claude 키를 회전할까요?". */
  title: ReactNode;
  /** 보조 설명 — 결과로 어떤 일이 일어나는지 한국어 존댓말. */
  description?: ReactNode;
  /** 운영자가 입력해야 정확히 일치해야 하는 토큰 — 보통 ``ROTATE`` / ``DELETE``. */
  confirmation: string;
  /** Confirm 버튼 라벨 — 미지정 시 "계속". */
  confirmLabel?: string;
  /** Confirm 클릭 시 실행되는 핸들러. 비동기 가능. */
  onConfirm: () => void | Promise<void>;
  /** 위험도 — 기본 destructive(빨강). */
  tone?: "destructive" | "warning";
  /** 외부에서 진행 중 상태 강제 — 외부 mutation 훅과 결합하려 할 때. */
  isPending?: boolean;
  /** 추가 본문(자식). 보통 영향 분석 또는 dry-run 결과 요약. */
  children?: ReactNode;
}

export function ConfirmGate({
  open,
  onOpenChange,
  title,
  description,
  confirmation,
  confirmLabel = "계속",
  onConfirm,
  tone = "destructive",
  isPending,
  children,
}: ConfirmGateProps) {
  const [input, setInput] = useState("");
  const [internalPending, setInternalPending] = useState(false);
  const pending = !!isPending || internalPending;
  const valid = input === confirmation;

  // 진입할 때마다 입력값 초기화 — 다른 항목 confirm에 누설되지 않도록.
  useEffect(() => {
    if (open) setInput("");
  }, [open]);

  async function handleConfirm() {
    if (!valid || pending) return;
    setInternalPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } finally {
      setInternalPending(false);
    }
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      alert
      dismissible={!pending}
      size="sm"
      footer={
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            취소
          </Button>
          <Button
            variant={tone === "destructive" ? "destructive" : "primary"}
            size="sm"
            onClick={handleConfirm}
            disabled={!valid || pending}
          >
            {pending ? "진행 중…" : confirmLabel}
          </Button>
        </>
      }
    >
      {children && <div className="mb-4">{children}</div>}
      <label className="block text-xs text-[--muted-foreground]">
        계속하려면{" "}
        <code className="rounded-[--radius-sm] border border-[--border-divider] bg-[--surface] px-1 py-0.5 font-mono text-[11px] text-[--foreground]">
          {confirmation}
        </code>
        를 그대로 입력해 주세요.
      </label>
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        disabled={pending}
        spellCheck={false}
        autoComplete="off"
        autoCapitalize="off"
        className="mt-2 w-full rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-2 font-mono text-sm text-[--foreground] outline-none focus:border-[--primary] focus:ring-2 focus:ring-[--ring] disabled:opacity-50"
        aria-label={`확인 입력 — ${confirmation}`}
      />
    </Modal>
  );
}
