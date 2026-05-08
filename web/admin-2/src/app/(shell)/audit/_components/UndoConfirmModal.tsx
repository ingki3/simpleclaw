"use client";

/**
 * UndoConfirmModal — admin.pen `nHHuf` (변경 되돌리기) 시각 spec.
 *
 * 구성 (위 → 아래):
 *  1) 헤더 — ↶ 아이콘 + "변경 되돌리기" + 닫기.
 *  2) 요약 카드 — 엔티티(target) / 필드(field) / 변경(before → after).
 *  3) 안내문 — Undo 가 일으킬 부수효과를 한 줄로 명시.
 *  4) 푸터 — 취소 / 되돌리기 (primary).
 *
 * Undo 는 위험 등급이 시크릿 회전보다 낮으므로 ConfirmGate (키워드+카운트다운) 는
 * 사용하지 않는다 — 단일 클릭 confirm + ESC/백드롭 cancel 만으로 충분.
 * 시크릿 회전 같은 위험 등급은 별도 ConfirmGate flow (BIZ-120) 가 처리.
 */

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { Modal } from "./Modal";
import type { AuditEntry } from "../_data";

interface UndoConfirmModalProps {
  open: boolean;
  entry: AuditEntry | null;
  onClose: () => void;
  /** 사용자가 "되돌리기" 를 확정 — 부모가 실제 mutation 수행. */
  onConfirm: (entry: AuditEntry) => void;
}

export function UndoConfirmModal({
  open,
  entry,
  onClose,
  onConfirm,
}: UndoConfirmModalProps) {
  if (!entry) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="md"
      data-testid="undo-confirm-modal"
      title={
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-lg text-(--muted-foreground)">
            ↶
          </span>
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            변경 되돌리기
          </h2>
          <Badge tone="warning" size="sm">
            {entry.action}
          </Badge>
        </div>
      }
      footer={
        <>
          <Button
            size="sm"
            variant="secondary"
            onClick={onClose}
            data-testid="undo-confirm-cancel"
          >
            취소
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => onConfirm(entry)}
            data-testid="undo-confirm-submit"
          >
            되돌리기
          </Button>
        </>
      }
    >
      <section
        data-testid="undo-confirm-summary"
        className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-(--radius-l) border border-(--border) bg-(--surface) p-4 text-sm"
      >
        <span className="text-(--muted-foreground)">엔티티</span>
        <span
          data-testid="undo-confirm-target"
          className="text-right font-mono text-(--foreground)"
        >
          {entry.target}
        </span>

        {entry.field ? (
          <>
            <span className="text-(--muted-foreground)">필드</span>
            <span
              data-testid="undo-confirm-field"
              className="text-right font-mono text-(--foreground)"
            >
              {entry.field}
            </span>
          </>
        ) : null}

        <span className="text-(--muted-foreground)">변경</span>
        <span className="text-right font-mono">
          {entry.before !== undefined ? (
            <span
              data-testid="undo-confirm-before"
              className="text-(--color-error)"
            >
              {entry.before}
            </span>
          ) : (
            <span className="text-(--muted-foreground)">—</span>
          )}
          {entry.before !== undefined && entry.after !== undefined ? (
            <span aria-hidden className="px-1 text-(--muted-foreground)">
              →
            </span>
          ) : null}
          {entry.after !== undefined ? (
            <span
              data-testid="undo-confirm-after"
              className="text-(--foreground)"
            >
              {entry.after}
            </span>
          ) : null}
        </span>
      </section>

      <p
        data-testid="undo-confirm-note"
        className="rounded-(--radius-m) border border-(--border) bg-(--card) p-3 text-xs text-(--muted-foreground)"
      >
        되돌리기를 실행하면 위 변경이 직전 상태로 복귀하며, 부수효과(데몬 재시작·캐시
        무효화 등) 는 별도 액션으로 audit 에 다시 기록됩니다.
      </p>
    </Modal>
  );
}
