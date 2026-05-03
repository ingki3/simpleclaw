/**
 * QuickLinkCard — 영역 페이지로 점프하는 카드형 링크.
 *
 * DESIGN.md §4.6 Empty/CTA를 차용 — 아이콘 + 라벨 + 한 줄 설명. 링크 자체가 카드라
 * 키보드 포커스 순서가 자연스럽다(헤더 후 카드 그리드 → 리스트).
 */

import Link from "next/link";
import { cn } from "@/lib/cn";
import { getIcon } from "@/lib/icon";
import { ChevronRight } from "lucide-react";

export interface QuickLinkCardProps {
  href: string;
  title: string;
  description: string;
  /** lucide 아이콘 이름 — `nav.ts`의 NavItem.icon과 동일 어휘. */
  icon: string;
  className?: string;
}

export function QuickLinkCard({
  href,
  title,
  description,
  icon,
  className,
}: QuickLinkCardProps) {
  const Icon = getIcon(icon);
  return (
    <Link
      href={href}
      className={cn(
        "group flex items-start gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-4 transition-colors hover:bg-(--surface) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--ring)",
        className,
      )}
    >
      <span
        aria-hidden
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-(--radius-m) bg-(--primary-tint) text-(--primary)"
      >
        <Icon size={18} strokeWidth={1.75} />
      </span>
      <span className="flex flex-1 flex-col gap-0.5 min-w-0">
        <span className="text-sm font-semibold text-(--foreground-strong)">
          {title}
        </span>
        <span className="text-xs text-(--muted-foreground) line-clamp-2">
          {description}
        </span>
      </span>
      <ChevronRight
        size={16}
        aria-hidden
        className="mt-1 shrink-0 text-(--muted-foreground) transition-transform group-hover:translate-x-0.5"
      />
    </Link>
  );
}
