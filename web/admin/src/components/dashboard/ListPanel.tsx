/**
 * ListPanel — 대시보드 우측의 리스트형 카드(감사 로그·에러 로그)의 공용 컨테이너.
 *
 * 헤더 라인(h2 + 우측 슬롯) + 본문 슬롯 + footer 슬롯의 단순 구조.
 * 본문 안의 로딩/빈 상태/에러는 호출부에서 결정하므로 그대로 children으로 받는다.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface ListPanelProps {
  title: string;
  /** 헤더 우측 — 보통 "전체 보기" 링크. */
  headerRight?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function ListPanel({
  title,
  headerRight,
  children,
  className,
}: ListPanelProps) {
  return (
    <section
      className={cn(
        "flex flex-col rounded-[--radius-l] border border-[--border] bg-[--card]",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-2 border-b border-[--border] px-4 py-3">
        <h2 className="text-sm font-semibold text-[--foreground-strong]">
          {title}
        </h2>
        {headerRight}
      </header>
      <div className="flex flex-col">{children}</div>
    </section>
  );
}

/** 패널 본문에서 사용하는 공용 빈/로딩/에러 표시 한 줄. */
export function PanelMessage({
  tone = "muted",
  children,
}: {
  tone?: "muted" | "error";
  children: ReactNode;
}) {
  const className =
    tone === "error"
      ? "px-4 py-6 text-sm text-[--color-error]"
      : "px-4 py-6 text-sm text-[--muted-foreground]";
  return (
    <p className={className} role={tone === "error" ? "alert" : undefined}>
      {children}
    </p>
  );
}
