"use client";

/**
 * RestartBanner — DESIGN.md §4.9 Restart Required.
 *
 * 페이지 상단에 끼는 알림 바. process-restart가 필요한 변경이 누적되었을 때
 * "지금 재시작 / 다음 시작 시 적용" 두 옵션을 노출한다.
 *
 * 본 1차 스캐폴딩에서는 5단계 stepper(저장→unmount→wait→restart→health)는
 * 모달 컴포넌트로 분리해 후속 이슈에서 만든다 — 여기서는 트리거만 제공.
 */

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { cn } from "@/lib/cn";

export interface RestartBannerProps {
  /** 재시작이 필요한 변경 갯수. 0이면 본 컴포넌트는 null을 반환한다. */
  pending: number;
  onRestartNow: () => void;
  onDeferUntilNextStart: () => void;
  className?: string;
}

export function RestartBanner({
  pending,
  onRestartNow,
  onDeferUntilNextStart,
  className,
}: RestartBannerProps) {
  if (pending <= 0) return null;
  return (
    <div
      role="alert"
      aria-live="polite"
      className={cn(
        "flex items-center gap-3 rounded-(--radius-m) border border-(--color-warning) bg-(--color-warning-bg) px-4 py-3 text-sm text-(--foreground)",
        className,
      )}
    >
      <AlertTriangle
        size={16}
        aria-hidden
        className="text-(--color-warning)"
      />
      <div className="flex-1">
        <strong className="font-medium">데몬 재시작이 필요합니다.</strong>{" "}
        <span className="text-(--muted-foreground)">
          저장된 변경 {pending}건이 다음 재시작 시 적용됩니다.
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onDeferUntilNextStart}>
          다음 시작 시 적용
        </Button>
        <Button variant="primary" size="sm" onClick={onRestartNow}>
          지금 재시작
        </Button>
      </div>
    </div>
  );
}
