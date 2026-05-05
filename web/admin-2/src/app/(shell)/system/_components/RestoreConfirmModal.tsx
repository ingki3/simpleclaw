"use client";

/**
 * RestoreConfirmModal — admin.pen `WuWqC` (System Restore Confirm) 박제.
 *
 * BackupDetailModal 의 "이 백업으로 복원" 또는 BackupListCard 의 "복원…" 트리거 시 진입.
 * DESIGN.md §4.2 / §4.9 ConfirmGate + Restart Required 패턴을 합친 화면:
 *  - 영향 범위(데몬 재시작 + persona/memory 덮어쓰기) 명시.
 *  - 5단계 stepper(stop daemon → backup current → restore → integrity → start daemon).
 *  - dry-run 옵션 — 미적용 preview 만 산출.
 *  - 복원 진행은 ConfirmGate 5초 카운트다운 + 키워드 ("restore") 입력 후 활성화.
 */

import { useEffect, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { Code } from "@/design/atoms/Code";
import { ConfirmGate } from "@/design/molecules/ConfirmGate";
import { cn } from "@/lib/cn";
import { Modal } from "./Modal";
import { formatTimestamp } from "./BackupListCard";
import type { BackupEntry } from "../_data";

interface RestoreConfirmModalProps {
  open: boolean;
  backup: BackupEntry | null;
  onClose: () => void;
  /** ConfirmGate 통과 시 호출. */
  onConfirm: (backup: BackupEntry) => void;
  /** dry-run 클릭 시 호출 — 부모가 별도 preview 화면을 노출하거나 console mock. */
  onDryRun?: (backup: BackupEntry) => void;
}

const STEPS: readonly { key: string; label: string; description: string }[] = [
  {
    key: "stop",
    label: "1. stop",
    description: "데몬을 안전 종료합니다.",
  },
  {
    key: "snapshot",
    label: "2. snapshot",
    description: "현재 상태를 자동 백업합니다 (롤백 안전망).",
  },
  {
    key: "restore",
    label: "3. restore",
    description: "선택한 백업의 config·persona·memory·skills 를 적용합니다.",
  },
  {
    key: "integrity",
    label: "4. integrity",
    description: "sha256 해시와 스키마 검증을 수행합니다.",
  },
  {
    key: "start",
    label: "5. start",
    description: "데몬을 다시 시작하고 헬스를 확인합니다.",
  },
];

export function RestoreConfirmModal({
  open,
  backup,
  onClose,
  onConfirm,
  onDryRun,
}: RestoreConfirmModalProps) {
  const [dryRunRequested, setDryRunRequested] = useState(false);

  useEffect(() => {
    if (open) setDryRunRequested(false);
  }, [open]);

  if (!backup) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      data-testid="restore-confirm-modal"
      width="lg"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            백업으로 복원
          </h2>
          <p className="font-mono text-xs text-(--muted-foreground)">
            {backup.filename}
          </p>
        </div>
      }
      footer={
        <Button variant="ghost" size="sm" onClick={onClose}>
          닫기
        </Button>
      }
      footerLeft={
        onDryRun ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              onDryRun(backup);
              setDryRunRequested(true);
            }}
            data-testid="restore-confirm-dryrun"
          >
            dry-run 미리보기
          </Button>
        ) : null
      }
    >
      <div
        className={cn(
          "flex flex-col gap-2 rounded-(--radius-l) border border-(--color-warning) bg-(--color-warning-bg) p-4",
        )}
        data-testid="restore-confirm-impact"
      >
        <h3 className="text-sm font-semibold text-(--color-warning)">영향 범위</h3>
        <ul className="flex flex-col gap-1 text-xs text-(--foreground)">
          <li>
            데몬 재시작 (~10초) — 진행 중 작업이 중단됩니다.
          </li>
          <li>
            <Code>persona</Code>·<Code>memory</Code>·<Code>config</Code> 가
            백업 시점({formatTimestamp(backup.timestamp)}) 으로 덮어쓰여집니다.
          </li>
          <li>
            현재 상태는 복원 직전 자동 백업 (snapshot 단계) 에 보존됩니다.
          </li>
        </ul>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-(--muted-foreground)">
          단계
        </h3>
        <ol
          data-testid="restore-confirm-steps"
          className="flex flex-col gap-2"
        >
          {STEPS.map((step) => (
            <li
              key={step.key}
              data-testid={`restore-confirm-step-${step.key}`}
              className="flex items-start gap-3 rounded-(--radius-m) border border-(--border) bg-(--surface) px-3 py-2"
            >
              <span className="font-mono text-xs font-semibold text-(--foreground-strong)">
                {step.label}
              </span>
              <span className="text-xs text-(--muted-foreground)">
                {step.description}
              </span>
            </li>
          ))}
        </ol>
      </div>

      {dryRunRequested ? (
        <p
          data-testid="restore-confirm-dryrun-result"
          className="rounded-(--radius-m) border border-(--color-info) bg-(--color-info-bg) px-3 py-2 text-xs text-(--color-info)"
        >
          dry-run 결과는 콘솔 또는 Logging 영역에서 확인하세요.
        </p>
      ) : null}

      <ConfirmGate
        keyword="restore"
        confirmLabel="복원 진행"
        cancelLabel="취소"
        onConfirm={() => {
          onConfirm(backup);
          onClose();
        }}
        onCancel={onClose}
        description={
          <span data-testid="restore-confirm-description">
            복원을 시작하려면 키워드 <Code>restore</Code> 를 입력하고 5초 카운트다운을 기다리세요.
          </span>
        }
      />
    </Modal>
  );
}
