"use client";

/**
 * RejectConfirmModal — admin.pen `lVcRk` (Reject Confirm + Blocklist) 박제.
 *
 * 인사이트 큐의 한 항목을 거절(=USER.md 에 적용 안 함)하면서 동시에 블록리스트에
 * 등록한다. 차단 기간은 단일 셀렉트 — `7d / 30d / forever` 중 하나 (admin.pen
 * spec).
 *
 * 거절 사유는 audit 로그용으로 선택 입력. 빈 사유로도 차단은 가능하지만,
 * 운영자가 나중에 "왜 차단했는지" 를 회상할 수 있도록 비어있을 때 placeholder
 * 로 안내한다.
 *
 * 재허용은 본 모달에서 다루지 않는다 — Blocklist 표의 "차단 해제" 액션이 별도
 * 책임 (BlocklistTable.tsx).
 */

import { useEffect, useState } from "react";
import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Select } from "@/design/atoms/Select";
import { Textarea } from "@/design/atoms/Textarea";
import type { MemoryInsight } from "../_data";
import { Modal } from "./Modal";

export type BlockDuration = "7d" | "30d" | "forever";

const DURATION_OPTIONS: ReadonlyArray<{ value: BlockDuration; label: string }> = [
  { value: "7d", label: "7일 차단" },
  { value: "30d", label: "30일 차단" },
  { value: "forever", label: "영구 차단" },
];

export interface RejectConfirmInput {
  insightId: string;
  topicKey: string;
  reason: string;
  duration: BlockDuration;
}

interface RejectConfirmModalProps {
  /** 거절할 인사이트 — null 이면 모달이 닫혀 있다. */
  target: MemoryInsight | null;
  onClose: () => void;
  onConfirm: (input: RejectConfirmInput) => void;
}

export function RejectConfirmModal({
  target,
  onClose,
  onConfirm,
}: RejectConfirmModalProps) {
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState<BlockDuration>("30d");

  // 새 target 이 들어오면 입력값 초기화 — 이전 reject 의 잔여값 차단.
  // target.id 만 의존성으로 두면 같은 항목 재오픈 시 입력 초기화가 안 일어나는데,
  // 그게 의도 (취소 후 즉시 다시 열면 적은 사유 보존).
  const targetId = target?.id;
  useEffect(() => {
    if (targetId) {
      setReason("");
      setDuration("30d");
    }
  }, [targetId]);

  return (
    <Modal
      open={!!target}
      onClose={onClose}
      width="md"
      data-testid="reject-confirm-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-base font-semibold text-(--foreground-strong)">
            제안을 거절할까요?
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            거절하면 다음 dreaming 사이클부터 동일 토픽이 자동으로 차단됩니다.
          </p>
        </div>
      }
      footer={
        <>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            data-testid="reject-confirm-cancel"
          >
            취소
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={!target}
            onClick={() => {
              if (!target) return;
              onConfirm({
                insightId: target.id,
                topicKey: normalizeTopic(target.topic),
                reason: reason.trim(),
                duration,
              });
            }}
            data-testid="reject-confirm-submit"
          >
            거절하고 블록
          </Button>
        </>
      }
    >
      {target ? (
        <div
          className="rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-xs"
          data-testid="reject-confirm-target"
        >
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-(--muted-foreground)">
              {target.topic}
            </span>
            <Badge tone="brand" size="sm">
              {target.lifecycle}
            </Badge>
            <Badge tone="neutral" size="sm">
              {target.channel}
            </Badge>
          </div>
          <p className="mt-1 break-words text-(--foreground)">{target.text}</p>
        </div>
      ) : null}

      <label className="flex flex-col gap-1 text-xs text-(--muted-foreground)">
        <span>차단 기간 (admin.pen 단일 셀렉트)</span>
        <Select
          value={duration}
          onChange={(e) =>
            setDuration(e.currentTarget.value as BlockDuration)
          }
          options={DURATION_OPTIONS as { value: string; label: string }[]}
          data-testid="reject-confirm-duration"
        />
      </label>

      <label className="flex flex-col gap-1 text-xs text-(--muted-foreground)">
        <span>거절 사유 (선택, audit 로그에 남아요)</span>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          rows={3}
          placeholder="예: 일회성 농담 — 학습할 가치 없음"
          data-testid="reject-confirm-reason"
        />
      </label>
    </Modal>
  );
}

/** 토픽 키 정규형 — 블록리스트의 case-insensitive 비교용. */
export function normalizeTopic(topic: string): string {
  return topic.trim().toLowerCase();
}
