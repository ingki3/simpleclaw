/**
 * ConfigSnapshotCard — admin.pen `AznDq` (cardConfigSnap) 박제.
 *
 * 활성 버전 한 줄 + yaml 발췌 code block + 다운로드/이전 버전 복원 버튼 행.
 * 코드 발췌는 시크릿 마스킹된 상태로만 노출 — 본 단계는 정적 fixture.
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { Code } from "@/design/atoms/Code";
import { cn } from "@/lib/cn";
import type { ConfigSnapshotInfo } from "../_data";

interface ConfigSnapshotCardProps {
  info: ConfigSnapshotInfo;
  /** "다운로드" 클릭 — 부모가 실제 fetch 트리거. */
  onDownload?: () => void;
  /** "이전 버전 복원…" 클릭 — 부모가 별도 흐름으로 위임. */
  onRestoreVersion?: () => void;
  className?: string;
}

export function ConfigSnapshotCard({
  info,
  onDownload,
  onRestoreVersion,
  className,
}: ConfigSnapshotCardProps) {
  return (
    <section
      data-testid="config-snapshot-card"
      aria-label="config.yaml 스냅샷"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">
        config.yaml 스냅샷
      </h2>

      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-(--muted-foreground)">활성 버전</span>
        <span className="font-mono text-(--foreground)">
          {info.activeVersion}
        </span>
      </div>

      <Code block data-testid="config-snapshot-excerpt">
        {info.excerpt}
      </Code>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={onDownload}
          data-testid="config-snapshot-download"
        >
          다운로드
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={onRestoreVersion}
          data-testid="config-snapshot-restore-version"
        >
          이전 버전 복원…
        </Button>
      </div>
    </section>
  );
}
