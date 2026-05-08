/**
 * RestartCard — admin.pen `k6R7yU` (cardRestart) 박제.
 *
 * 본문:
 *  1) 한 줄 안내 (영향 요약).
 *  2) 운영자 컨펌 배너 (production 등에서만 노출).
 *  3) 마지막 재시작 시각 + Δ.
 *  4) destructive 버튼 — 클릭 시 부모가 ConfirmRestartDialog 를 연다.
 *
 * 시스템 운영자가 "지금 재시작해도 괜찮은가?" 를 결정할 수 있는 컨텍스트 (헬스/시각)
 * 를 모두 한 카드에서 제공한다.
 */
"use client";

import { Button } from "@/design/atoms/Button";
import { cn } from "@/lib/cn";
import type { RestartInfo } from "../_data";

interface RestartCardProps {
  info: RestartInfo;
  /** 클릭 시 부모가 ConfirmRestartDialog 를 연다. */
  onRestartClick: () => void;
  className?: string;
}

export function RestartCard({ info, onRestartClick, className }: RestartCardProps) {
  return (
    <section
      data-testid="restart-card"
      aria-label="재시작 액션"
      className={cn(
        "flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-5 shadow-(--shadow-sm)",
        className,
      )}
    >
      <h2 className="text-sm font-semibold text-(--foreground-strong)">
        재시작 액션
      </h2>
      <p className="text-xs text-(--muted-foreground)">{info.impactSummary}</p>

      {info.needsOperatorConfirm ? (
        <div
          data-testid="restart-card-warning"
          role="note"
          className="flex items-center gap-2 rounded-(--radius-m) bg-(--color-warning-bg) px-3 py-2 text-xs text-(--color-warning)"
        >
          <span aria-hidden>⚠</span>
          <span className="font-medium">
            prod 환경 — 실행 전 운영자 컨펌 필요
          </span>
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-(--muted-foreground)">마지막 재시작</span>
        <span className="font-mono text-(--foreground)">
          {info.lastRestart} ({info.lastRestartRelative})
        </span>
      </div>

      <Button
        variant="destructive"
        size="sm"
        onClick={onRestartClick}
        data-testid="restart-card-trigger"
      >
        데몬 재시작…
      </Button>
    </section>
  );
}
