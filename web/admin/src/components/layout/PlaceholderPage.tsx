/**
 * PlaceholderPage — 1차 스캐폴딩에서 11개 영역 페이지의 공통 빈 상태.
 *
 * DESIGN.md §4.6 Empty / First-run의 가이드를 단순화해 사용한다.
 * 실제 컨텐츠는 후속 이슈에서 각 영역별로 채워진다.
 */

import { getIcon } from "@/lib/icon";

export interface PlaceholderPageProps {
  /** lucide 아이콘 이름. */
  icon: string;
  title: string;
  description: string;
}

export function PlaceholderPage({
  icon,
  title,
  description,
}: PlaceholderPageProps) {
  const Icon = getIcon(icon);
  return (
    <section className="mx-auto flex max-w-[480px] flex-col items-center gap-3 rounded-(--radius-l) border border-dashed border-(--border-strong) bg-(--card) px-8 py-16 text-center">
      <Icon
        size={32}
        strokeWidth={1.5}
        aria-hidden
        className="text-(--muted-foreground)"
      />
      <h2 className="text-xl font-semibold text-(--foreground-strong)">
        {title}
      </h2>
      <p className="text-sm text-(--muted-foreground)">{description}</p>
    </section>
  );
}
