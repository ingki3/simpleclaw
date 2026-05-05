"use client";

/**
 * TokenRotateModal — admin.pen `Dtxhd` (Token Rotate ConfirmGate) 를 React 로 박제.
 *
 * Telegram Bot Token 회전은 *위험 등급* 작업이라 운영자에게 두 단계 확인을 요구한다 —
 *  1) 키워드 `ROTATE` 직접 입력
 *  2) 입력 일치 후 카운트다운 (기본 3초)
 *
 * 시각: 헤더에 빨간 회전 아이콘 + 검은 박스의 경고문, 그리고 진행률 바 +
 * 우하단 "회전(N)" 버튼 — admin.pen 시각 spec 그대로.
 *
 * 본 단계는 onConfirm 호출 시 부모가 console 박제로 처리. 데몬 통합 단계에서
 * 실제 회전 API 호출 + 토스트 + status 갱신.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Input } from "@/design/atoms/Input";
import { cn } from "@/lib/cn";
import { Modal } from "./Modal";

interface TokenRotateModalProps {
  open: boolean;
  /** 회전 대상 봇 이름 — 헤더에 표시 (예: `Telegram Bot`). */
  targetLabel: string;
  /** 입력해야 할 키워드 — 기본 `ROTATE`. */
  keyword?: string;
  /** 키워드 일치 후 활성화까지 대기 초 — 기본 3. */
  countdownSeconds?: number;
  onClose: () => void;
  onConfirm: () => void;
}

export function TokenRotateModal({
  open,
  targetLabel,
  keyword = "ROTATE",
  countdownSeconds = 3,
  onClose,
  onConfirm,
}: TokenRotateModalProps) {
  const [typed, setTyped] = useState("");
  const [remaining, setRemaining] = useState(countdownSeconds);

  // 모달이 새로 열릴 때마다 입력/카운트다운을 초기화 — race 보호.
  useEffect(() => {
    if (open) {
      setTyped("");
      setRemaining(countdownSeconds);
    }
  }, [open, countdownSeconds]);

  const matched = typed.trim() === keyword;

  // 키워드가 정확히 일치하기 시작한 시점부터만 카운트다운 진행.
  useEffect(() => {
    if (!open) return;
    if (!matched) {
      setRemaining(countdownSeconds);
      return;
    }
    if (remaining <= 0) return;
    const id = setTimeout(() => setRemaining((s) => s - 1), 1000);
    return () => clearTimeout(id);
  }, [open, matched, remaining, countdownSeconds]);

  const ready = matched && remaining === 0;
  // 진행률 — 0 ~ 1. 미일치 시 0, 일치 후 시간 경과에 따라 채워진다.
  const progress = matched
    ? 1 - remaining / Math.max(countdownSeconds, 1)
    : 0;

  const handleConfirm = () => {
    if (!ready) return;
    onConfirm();
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="sm"
      data-testid="token-rotate-modal"
      title={
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-(--color-error)">
            ↻
          </span>
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            토큰 회전 — {targetLabel}
          </h2>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="token-rotate-cancel"
          >
            취소
          </Button>
          <Button
            variant="destructive"
            disabled={!ready}
            onClick={handleConfirm}
            data-testid="token-rotate-confirm"
          >
            회전 ({remaining})
          </Button>
        </>
      }
    >
      <div
        role="alert"
        data-testid="token-rotate-warning"
        className="flex items-start gap-2 rounded-(--radius-m) bg-black px-3 py-2 text-sm text-(--color-error)"
      >
        <span aria-hidden className="mt-0.5">
          ⚠
        </span>
        <p>
          기존 토큰은 즉시 무효화됩니다. 외부 봇이 사용 중인 토큰이 있다면
          모두 재설정 필요.
        </p>
      </div>

      <label className="flex flex-col gap-1.5 text-sm text-(--foreground)">
        <span>
          확인을 위해 <code className="font-mono">{keyword}</code> 입력
        </span>
        <Input
          value={typed}
          onChange={(e) => setTyped(e.currentTarget.value)}
          placeholder={keyword}
          aria-label="confirm keyword"
          error={typed.length > 0 && !matched}
          data-testid="token-rotate-keyword"
          autoFocus
        />
      </label>

      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-(--muted-foreground)">실행까지</span>
        <span
          aria-live="polite"
          data-testid="token-rotate-remaining"
          className={cn(
            "font-mono tabular-nums",
            matched ? "text-(--color-error)" : "text-(--muted-foreground)",
          )}
        >
          {remaining}초
        </span>
      </div>
      <div
        aria-hidden
        className="h-1 w-full overflow-hidden rounded-(--radius-pill) bg-(--surface)"
      >
        <div
          data-testid="token-rotate-progress"
          className="h-full bg-(--color-error) transition-all duration-1000 ease-linear"
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>
    </Modal>
  );
}
