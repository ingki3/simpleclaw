"use client";

/**
 * DryRunFooter — DESIGN.md §4.1 Sticky Bar + §4.3 Dry-run Preview의 footer 변종.
 *
 * 좌측: "변경사항 저장 안 됨" 인디케이터 + dry-run 요약 텍스트.
 * 우측: Cancel / Dry-run / Apply 트리오. dry-run을 거치지 않으면 apply는 disabled.
 */

import { Button } from "@/components/atoms/Button";
import { cn } from "@/lib/cn";

export interface DryRunFooterProps {
  /** 미저장 상태인지 여부 — 좌측 인디케이터 색을 바꾼다. */
  dirty: boolean;
  /** dry-run을 통과했는지. apply 버튼의 활성을 결정한다. */
  dryRunPassed: boolean;
  /** dry-run 요약 — 통과 시 "1시간 트래픽 12건 차단됨" 같은 영향 요약을 노출. */
  summary?: string;
  onCancel: () => void;
  onDryRun: () => void;
  onApply: () => void;
  className?: string;
}

export function DryRunFooter({
  dirty,
  dryRunPassed,
  summary,
  onCancel,
  onDryRun,
  onApply,
  className,
}: DryRunFooterProps) {
  return (
    <div className={cn("flex w-full items-center gap-3", className)}>
      <div className="flex items-center gap-2 text-xs text-(--muted-foreground)">
        <span
          aria-hidden
          className={cn(
            "inline-block h-2 w-2 rounded-(--radius-pill)",
            dirty ? "bg-(--color-warning)" : "bg-(--muted-foreground)",
          )}
        />
        <span>
          {dirty ? "변경사항 저장 안 됨" : "저장 완료"}
          {summary ? ` · ${summary}` : null}
        </span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          취소
        </Button>
        <Button variant="secondary" size="sm" onClick={onDryRun}>
          Dry-run
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={onApply}
          disabled={!dryRunPassed}
          title={dryRunPassed ? undefined : "먼저 Dry-run을 실행하세요"}
        >
          적용
        </Button>
      </div>
    </div>
  );
}
