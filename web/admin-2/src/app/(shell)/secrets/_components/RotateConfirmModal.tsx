"use client";

/**
 * RotateConfirmModal — admin.pen `M99Mh` Rotate ConfirmGate (BIZ-109 P1) 박제.
 *
 * 회전은 시크릿 도메인의 위험 등급 작업이라 ConfirmGate (molecule) 를 그대로
 * 사용한다 — 운영자가 키 이름을 정확히 입력하고, 키워드 일치 후 카운트다운이
 * 끝나야 destructive 버튼이 활성화된다 (DESIGN.md §4.2 + §3.2 ConfirmGate).
 *
 * 본 모달은 *키 회전 전* 단계만 책임진다 — admin.pen spec 의 "ConfirmGate →
 * 새 토큰 생성/입력 → ping 검증" 중 1단계. 새 토큰 입력/검증은 후속 sub-issue
 * 의 책임이고, 본 단계는 onConfirm 시 부모가 console 박제 + maskedPreview 만
 * 회전된 것으로 갱신한다 (실제 keyring rotate 호출은 미연결).
 *
 * a11y: alertdialog role + aria-labelledby + aria-describedby (DESIGN.md §10.2).
 */

import { Badge } from "@/design/atoms/Badge";
import { ConfirmGate } from "@/design/molecules/ConfirmGate";
import { PolicyChip } from "@/design/molecules/PolicyChip";
import type { SecretRecord } from "../_data";
import { Modal } from "./Modal";

interface RotateConfirmModalProps {
  /** 회전 대상 — null 이면 모달이 닫혀 있다. */
  target: SecretRecord | null;
  onClose: () => void;
  /** ConfirmGate 통과 시 호출. 부모가 fixture/state 갱신 + 콘솔 박제 담당. */
  onConfirm: (secret: SecretRecord) => void;
  /** 카운트다운 초 — 테스트에서 1로 줄여 사용. 기본 5. */
  countdownSeconds?: number;
}

export function RotateConfirmModal({
  target,
  onClose,
  onConfirm,
  countdownSeconds = 5,
}: RotateConfirmModalProps) {
  const open = target !== null;
  return (
    <Modal
      open={open}
      onClose={onClose}
      width="md"
      role="alertdialog"
      data-testid="rotate-confirm-modal"
      title={
        <div
          id="rotate-confirm-title"
          className="flex flex-col gap-0.5"
        >
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            시크릿을 회전할까요?
          </h2>
          <p
            id="rotate-confirm-description"
            className="text-xs text-(--muted-foreground)"
          >
            회전하면 이전 값은 즉시 무효화되고, 새 값이 발급되어 keyring 에 기록됩니다.
            정책에 따라 일부 서비스는 재시작이 필요해요.
          </p>
        </div>
      }
      footer={null}
    >
      {target ? (
        <>
          <div
            data-testid="rotate-confirm-target"
            className="flex flex-col gap-2 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2 text-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-(--foreground)">
                {target.keyName}
              </span>
              <Badge tone="neutral" size="sm">
                {target.maskedPreview}
              </Badge>
              <PolicyChip kind={target.policy} />
            </div>
            {target.note ? (
              <p className="break-words text-(--muted-foreground)">
                {target.note}
              </p>
            ) : null}
            <p className="text-(--muted-foreground)">
              마지막 회전:{" "}
              <RelativeOrNever iso={target.lastRotatedAt} />
            </p>
          </div>

          <div data-testid="rotate-confirm-gate">
            <ConfirmGate
              keyword={target.keyName}
              countdownSeconds={countdownSeconds}
              confirmLabel="회전 실행"
              cancelLabel="취소"
              onCancel={onClose}
              onConfirm={() => onConfirm(target)}
              description={
                <>
                  위험 등급 작업입니다. 진행하려면 정확한 키 이름{" "}
                  <code className="font-mono">{target.keyName}</code> 을 입력하세요.
                  {" "}일치한 시점부터 {countdownSeconds}초 후 실행 버튼이 활성화됩니다.
                </>
              }
            />
          </div>
        </>
      ) : null}
    </Modal>
  );
}

function RelativeOrNever({ iso }: { iso: string | null }) {
  if (!iso) return <span>회전 이력 없음</span>;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return <span>—</span>;
  const diff = Date.now() - t;
  const day = Math.floor(diff / (24 * 60 * 60 * 1000));
  if (day < 1) return <span>오늘</span>;
  if (day < 30) return <span>{day}일 전</span>;
  return <span>{Math.floor(day / 30)}개월 전</span>;
}
