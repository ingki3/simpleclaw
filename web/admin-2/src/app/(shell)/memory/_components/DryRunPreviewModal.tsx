"use client";

/**
 * DryRunPreviewModal — admin.pen `QBf7N` (Screen 06.5-C · Dry-run Preview) 박제.
 *
 * "지금 dreaming 을 돌리면 USER.md 가 어떻게 바뀔까?" 를 미리 보여주는 비파괴
 * 시뮬레이션. promote / edit / archive / block 4종 변화를 한 카드씩 노출하고,
 * 카드 자체는 reusable `DryRunCard` (Molecular) 를 사용한다.
 *
 * 본 단계는 데몬 미연결 — 시뮬레이션 결과는 fixture (`_data.ts` DRY_RUN). 실제
 * 적용 액션은 부모(page) 가 fixture 카피만 갱신하고 토스트로 박제.
 */

import { Badge } from "@/design/atoms/Badge";
import { Button } from "@/design/atoms/Button";
import { DryRunCard } from "@/design/molecules/DryRunCard";
import { EmptyState } from "@/design/molecules/EmptyState";
import type { DryRunChange, DryRunPreview } from "../_data";
import { formatRelative } from "./ActiveProjectsPanel";
import { Modal } from "./Modal";

interface DryRunPreviewModalProps {
  open: boolean;
  onClose: () => void;
  preview: DryRunPreview;
  /** "변경 적용" — 본 단계는 fixture 갱신 + 토스트 stub. */
  onApply?: () => void;
}

const KIND_LABEL: Record<DryRunChange["kind"], string> = {
  promote: "신규 채택",
  edit: "본문 갱신",
  archive: "보관",
  block: "차단",
};

const KIND_TONE: Record<
  DryRunChange["kind"],
  "success" | "info" | "neutral" | "danger"
> = {
  promote: "success",
  edit: "info",
  archive: "neutral",
  block: "danger",
};

export function DryRunPreviewModal({
  open,
  onClose,
  preview,
  onApply,
}: DryRunPreviewModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      width="lg"
      data-testid="dry-run-modal"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-base font-semibold text-(--foreground-strong)">
            Dry-run Preview · 변경 미리보기
          </h2>
          <p className="text-xs text-(--muted-foreground)">
            지금 dreaming 을 돌리면 USER.md 가 어떻게 바뀔지 비파괴로 시뮬레이션
            합니다 (실제 적용 없음).
          </p>
        </div>
      }
      footerLeft={
        <span
          data-testid="dry-run-meta"
          className="text-xs text-(--muted-foreground)"
        >
          후보 {preview.candidateCount} · 변경{" "}
          {preview.changes.length}건 · 시뮬레이션 {formatRelative(preview.generatedAt)}
        </span>
      }
      footer={
        <>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            data-testid="dry-run-cancel"
          >
            닫기
          </Button>
          {onApply ? (
            <Button
              size="sm"
              variant="primary"
              onClick={onApply}
              disabled={preview.changes.length === 0}
              data-testid="dry-run-apply"
            >
              변경 적용
            </Button>
          ) : null}
        </>
      }
    >
      {preview.changes.length === 0 ? (
        <div data-testid="dry-run-empty">
          <EmptyState
            title="적용할 변경이 없어요"
            description="지금 dreaming 을 돌려도 USER.md 가 바뀌지 않습니다. 더 많은 대화가 쌓인 후 다시 시도해 주세요."
          />
        </div>
      ) : (
        <ul
          data-testid="dry-run-changes"
          className="flex flex-col gap-3"
        >
          {preview.changes.map((change) => (
            <li
              key={`${change.kind}-${change.topic}`}
              data-testid={`dry-run-change-${change.kind}-${change.topic}`}
              className="flex flex-col gap-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={KIND_TONE[change.kind]} size="sm">
                  {KIND_LABEL[change.kind]}
                </Badge>
                <span className="font-mono text-xs text-(--muted-foreground)">
                  {change.topic}
                </span>
              </div>
              <DryRunCard
                before={
                  <BeforeAfter
                    text={change.before}
                    placeholder="(USER.md 에 해당 토픽 없음)"
                  />
                }
                after={
                  <BeforeAfter
                    text={change.after}
                    placeholder={
                      change.kind === "block"
                        ? "(블록리스트로 이동 — USER.md 에 노출되지 않음)"
                        : change.kind === "archive"
                          ? "(아카이브로 이동 — USER.md 에서 제거)"
                          : "(변경 없음)"
                    }
                  />
                }
                impact={change.reason}
              />
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

function BeforeAfter({
  text,
  placeholder,
}: {
  text: string | null;
  placeholder: string;
}) {
  if (text === null || text.trim() === "") {
    return (
      <span className="italic text-(--muted-foreground)">{placeholder}</span>
    );
  }
  return <span className="text-(--foreground)">{text}</span>;
}
