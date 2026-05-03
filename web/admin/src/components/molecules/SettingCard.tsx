/**
 * SettingCard — DESIGN.md §4.1 Setting Edit Pattern의 한 카드.
 *
 * "한 카드 한 영역" 규약. 헤더(타이틀+설명+PolicyPill 슬롯) + 본문 + footer 슬롯.
 * 카드 우상단의 health dot은 §4.5 Health Surfacing의 "카드별 영역 상태" 표면을 뒤따른다.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { StatusPill, type StatusTone } from "@/components/atoms/StatusPill";

export interface SettingCardProps {
  title: string;
  /** 한 줄 설명 — DESIGN.md §6 voice & tone, 50자 이내 권장. */
  description?: string;
  /** 헤더 우측 슬롯 — 보통 PolicyPill 또는 적용 등급 표시. */
  headerRight?: ReactNode;
  /** 카드 영역 헬스 — 정의되지 않으면 표시하지 않는다. */
  health?: { tone: StatusTone; label: string };
  /** sticky footer — 보통 DryRunFooter 또는 Cancel/Apply 액션 쌍. */
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function SettingCard({
  title,
  description,
  headerRight,
  health,
  footer,
  children,
  className,
}: SettingCardProps) {
  return (
    <section
      className={cn(
        "flex flex-col rounded-[--radius-l] border border-[--border] bg-[--card]",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-4 px-6 pt-6">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-[--foreground-strong]">
            {title}
          </h2>
          {description ? (
            <p className="text-sm text-[--muted-foreground]">{description}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {health ? (
            <StatusPill tone={health.tone}>{health.label}</StatusPill>
          ) : null}
          {headerRight}
        </div>
      </header>

      <div className="flex flex-col gap-4 px-6 py-6">{children}</div>

      {footer ? (
        <footer className="flex items-center justify-end gap-2 rounded-b-[--radius-l] border-t border-[--border] bg-[--surface] px-6 py-3">
          {footer}
        </footer>
      ) : null}
    </section>
  );
}
