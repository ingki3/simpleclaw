"use client";

/**
 * BackupDetailModal — admin.pen `vQuZm` / `I9y17` (System Backup Detail) 박제.
 *
 * BackupListCard 의 행 클릭 시 진입. 백업 한 건의 메타(파일명 · 트리거 · 사이즈 ·
 * 포함 항목) + 무결성 해시 + 다운로드/복사 버튼을 노출한다. 푸터 우측 "이 백업으로
 * 복원" 클릭 시 부모가 RestoreConfirmModal 로 흐름을 넘긴다.
 */

import { Button } from "@/design/atoms/Button";
import { Code } from "@/design/atoms/Code";
import { Badge } from "@/design/atoms/Badge";
import { cn } from "@/lib/cn";
import { Modal } from "./Modal";
import { formatTimestamp } from "./BackupListCard";
import type { BackupEntry } from "../_data";

interface BackupDetailModalProps {
  open: boolean;
  backup: BackupEntry | null;
  onClose: () => void;
  /** "이 백업으로 복원" 클릭 — 부모가 RestoreConfirmModal 로 진입. */
  onRestore: (backup: BackupEntry) => void;
  /** "다운로드" 클릭. */
  onDownload?: (backup: BackupEntry) => void;
}

export function BackupDetailModal({
  open,
  backup,
  onClose,
  onRestore,
  onDownload,
}: BackupDetailModalProps) {
  if (!backup) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  const totalContents = backup.contents
    .map((c) => `${c.label}(${c.size})`)
    .join(" · ");

  const handleCopyHash = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      void navigator.clipboard.writeText(backup.sha256Short);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      data-testid="backup-detail-modal"
      width="lg"
      title={
        <div className="flex flex-col gap-0.5">
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            백업 상세
          </h2>
          <p className="font-mono text-xs text-(--muted-foreground)">
            {backup.filename}
          </p>
        </div>
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="backup-detail-close"
          >
            닫기
          </Button>
          <Button
            variant="primary"
            onClick={() => onRestore(backup)}
            data-testid="backup-detail-restore"
          >
            이 백업으로 복원
          </Button>
        </>
      }
    >
      <dl className="flex flex-col gap-2 text-sm">
        <Row
          label="생성 시각"
          value={
            <span className="font-mono text-(--foreground)">
              {formatTimestamp(backup.timestamp)}
            </span>
          }
        />
        <Row
          label="트리거"
          value={
            <Badge tone={backup.trigger === "manual" ? "info" : "neutral"}>
              {backup.trigger}
            </Badge>
          }
        />
        <Row
          label="크기"
          value={
            <span className="font-mono text-(--foreground)">
              {backup.sizeLabel}
            </span>
          }
        />
        <Row
          label="포함 항목"
          value={
            <span
              data-testid="backup-detail-contents"
              className="font-mono text-xs text-(--foreground)"
            >
              {totalContents}
            </span>
          }
        />
      </dl>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-(--muted-foreground)">
          무결성
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <Code data-testid="backup-detail-hash">{backup.sha256Short}</Code>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCopyHash}
            data-testid="backup-detail-copy-hash"
          >
            복사
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onDownload?.(backup)}
            data-testid="backup-detail-download"
          >
            다운로드
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Row({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-3")}>
      <dt className="text-xs text-(--muted-foreground)">{label}</dt>
      <dd className="flex items-center gap-2">{value}</dd>
    </div>
  );
}
