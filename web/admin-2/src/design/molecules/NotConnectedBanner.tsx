/**
 * NotConnectedBanner — Molecular. 페이지 헤더 직하 안내 배너 (BIZ-151).
 *
 * Admin 2.0 의 11개 영역은 _data.ts fixture 위에 박제되어 있다. 운영자가
 * 화면을 보고 "이미 동작 중인 데이터" 로 오해하지 않도록 페이지 상단에 명시적
 * 으로 "데몬 API 연결 대기" 를 알린다.
 *
 * tone:
 *  - "info"  — 데이터 소스가 아직 연결되지 않았음 (가장 흔한 케이스)
 *  - "warning" — 부분 연결 / 일부만 fixture (혼합 상태)
 *
 * 본 배너는 fixture 가 모두 운영 데이터로 교체되면 통째로 제거한다.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type NotConnectedTone = "info" | "warning";

const TONE: Record<NotConnectedTone, string> = {
  info: "border-(--color-info) bg-(--color-info-bg) text-(--color-info)",
  warning: "border-(--color-warning) bg-(--color-warning-bg) text-(--color-warning)",
};

const DOT: Record<NotConnectedTone, string> = {
  info: "bg-(--color-info)",
  warning: "bg-(--color-warning)",
};

export interface NotConnectedBannerProps {
  /** 한 줄 제목 — 기본 "데몬 API 연결 대기". */
  title?: ReactNode;
  /** 보조 설명 — 어떤 데이터 소스가 어떤 상태인지 한 줄로. */
  description?: ReactNode;
  tone?: NotConnectedTone;
  className?: string;
}

export function NotConnectedBanner({
  title = "데몬 API 연결 대기",
  description,
  tone = "info",
  className,
}: NotConnectedBannerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="not-connected-banner"
      data-tone={tone}
      className={cn(
        "flex items-start gap-3 rounded-(--radius-l) border border-dashed px-4 py-3 text-sm",
        TONE[tone],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "mt-1.5 inline-block h-2 w-2 shrink-0 rounded-(--radius-pill)",
          DOT[tone],
          "animate-pulse",
        )}
      />
      <div className="flex flex-col gap-1">
        <span className="font-medium">{title}</span>
        {description ? (
          <span className="text-(--muted-foreground)">{description}</span>
        ) : null}
      </div>
    </div>
  );
}
