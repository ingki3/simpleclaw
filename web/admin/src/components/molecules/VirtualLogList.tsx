"use client";

/**
 * VirtualLogList — 고정 행 높이 기반 단순 가상 스크롤.
 *
 * 왜 직접 만들었나: BIZ-47의 스킬 실행 로그는 20–수백 항목 규모이며, react-window를
 * 추가하면 의존성 1개 + RSC 호환 처리 비용이 든다. 본 1차에서는 행 높이가 모두 동일
 * (44px = compact table 행 높이)하다는 단순화 덕분에 80줄 미만으로 충분히 구현된다.
 *
 * 동작:
 *  - container scrollTop을 추적해 [start, end] 행 인덱스를 계산.
 *  - 위·아래로 ``OVERSCAN``개씩 더 렌더해 빠른 스크롤에서 빈 영역이 보이지 않게.
 *  - 외부 컨테이너 높이는 부모가 ``height`` prop으로 지정 — Drawer 같은 sticky 영역에선
 *    flex 부모가 줄어들지 않게 명시 height를 넘기는 편이 안정적이다.
 *
 * 한계:
 *  - 행 높이가 가변이면 본 구현은 깨진다. 그 경우 ResizeObserver 기반으로 재작성.
 */

import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

const OVERSCAN = 4;

export interface VirtualLogListProps<T> {
  items: readonly T[];
  /** 행 높이(px). DESIGN.md §4.7 compact 행 = 44. */
  rowHeight?: number;
  /** 컨테이너 높이(px) — 부모가 명시. */
  height: number;
  /** 행 키 추출. */
  getKey: (item: T, index: number) => string;
  renderRow: (item: T, index: number) => ReactNode;
  /** 빈 상태 슬롯 — items.length === 0일 때 노출. */
  emptyState?: ReactNode;
  className?: string;
}

export function VirtualLogList<T>({
  items,
  rowHeight = 44,
  height,
  getKey,
  renderRow,
  emptyState,
  className,
}: VirtualLogListProps<T>) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);

  // 마운트 직후 한 번 scrollTop을 read해 SSR 후 hydration mismatch를 피한다.
  useLayoutEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    setScrollTop(node.scrollTop);
  }, []);

  const total = items.length;
  const totalHeight = total * rowHeight;

  const { start, end } = useMemo(() => {
    if (total === 0) return { start: 0, end: 0 };
    const visibleCount = Math.ceil(height / rowHeight);
    const rawStart = Math.floor(scrollTop / rowHeight) - OVERSCAN;
    const rawEnd = Math.floor(scrollTop / rowHeight) + visibleCount + OVERSCAN;
    return {
      start: Math.max(0, rawStart),
      end: Math.min(total, rawEnd),
    };
  }, [scrollTop, height, rowHeight, total]);

  if (total === 0) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-(--radius-m) border border-dashed border-(--border-strong) bg-(--surface) text-sm text-(--muted-foreground)",
          className,
        )}
        style={{ height }}
      >
        {emptyState ?? <span>표시할 항목이 없습니다.</span>}
      </div>
    );
  }

  const slice = items.slice(start, end);
  const offsetY = start * rowHeight;

  return (
    <div
      ref={containerRef}
      onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      className={cn(
        "relative overflow-y-auto rounded-(--radius-m) border border-(--border) bg-(--card)",
        className,
      )}
      style={{ height }}
      role="list"
      aria-label="실행 로그 목록"
    >
      {/* 전체 높이 spacer — scrollbar 위치를 정확히 잡는다. */}
      <div style={{ height: totalHeight, position: "relative" }}>
        <div
          style={{
            transform: `translateY(${offsetY}px)`,
            position: "absolute",
            inset: 0,
            willChange: "transform",
          }}
        >
          {slice.map((item, i) => {
            const idx = start + i;
            return (
              <div
                key={getKey(item, idx)}
                role="listitem"
                style={{ height: rowHeight }}
              >
                {renderRow(item, idx)}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
