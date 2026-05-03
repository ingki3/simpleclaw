"use client";

/**
 * ConfirmGate — 위험·되돌릴 수 없는 변경 직전의 마지막 확인 모달.
 *
 * admin-requirements §3.2: 단일 운영자라도 "자기 자신의 실수"를 막기 위해
 * 한 번의 추가 확인이 필요하다. Cron의 경우:
 * - "Run now" — 단일 confirm (모달의 ``confirmLabel``만 노출)
 * - 잡 삭제 — 잡 이름을 그대로 입력해야 통과(``requireText`` prop)
 *
 * BIZ-43가 정식 ``ConfirmGate``를 제공하면 이 파일은 제거한다.
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { Modal } from "./Modal";

export interface ConfirmGateProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  /** 확인 버튼 라벨 — 동사 명령형 권장 ("실행", "삭제"). */
  confirmLabel: string;
  /** 채워 넣어야 통과되는 정확 문자열 — 위험 단계에서만 사용. */
  requireText?: string;
  /** 기본 ``destructive``; "Run now" 같은 비파괴 액션은 ``primary``. */
  tone?: "destructive" | "primary";
}

export function ConfirmGate({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel,
  requireText,
  tone = "destructive",
}: ConfirmGateProps) {
  const [typed, setTyped] = useState("");

  // 모달이 열릴 때마다 입력값을 초기화 — 이전 호출의 잔재가 남으면 위험.
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  const ready = !requireText || typed === requireText;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            취소
          </Button>
          <Button
            variant={tone === "destructive" ? "destructive" : "primary"}
            size="sm"
            disabled={!ready}
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {requireText ? (
        <div className="flex flex-col gap-2">
          <label className="text-sm text-(--muted-foreground)">
            계속하려면 <code className="rounded bg-(--surface) px-1 font-mono text-(--foreground-strong)">{requireText}</code> 을(를) 그대로 입력하세요.
          </label>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={requireText}
            autoFocus
          />
        </div>
      ) : (
        <p className="text-sm text-(--muted-foreground)">
          확인 후 즉시 적용돼요. 되돌릴 수 없는 변경이라면 한 번 더 점검해 주세요.
        </p>
      )}
    </Modal>
  );
}
