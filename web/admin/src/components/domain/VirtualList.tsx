"use client";

/**
 * VirtualList — 단순 windowed 리스트 렌더러.
 *
 * 의존성 추가 없이 1000+ 항목을 부드럽게 스크롤할 수 있게 한다. 각 행은 가변 높이가 아니라
 * `estimatedRowHeight`로 추정한 *동일 높이*를 사용한다 — 실제 행 높이는 다소 다를 수 있지만,
 * 본 화면의 MEMORY.md 항목은 짧은 bullet이라 1~3줄에 수렴한다는 가정이 깨지지 않는다.
 *
 * 임계 항목 수(`threshold`) 미만에서는 가상 스크롤을 끄고 자식을 그대로 펼친다 — 100건
 * 정도까지는 단순 렌더가 더 가볍고 a11y(검색·인쇄)에도 우호적이기 때문.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

export interface VirtualListProps<T> {
  items: ReadonlyArray<T>;
  /** 항목 1개 추정 높이(px). 화면 평균값 기준. */
  estimatedRowHeight: number;
  /** 가상 스크롤이 켜지는 항목 수 임계값(이상). */
  threshold?: number;
  /** 렌더 함수 — 동일한 시그니처로 데이터를 받아 React 노드를 반환. */
  renderItem: (item: T, index: number) => ReactNode;
  /** 컨테이너 최대 높이(px). 가상 스크롤 시 자체 스크롤 컨테이너가 된다. */
  maxHeight?: number;
  /** 가상 모드 시 양옆에 미리 그릴 행 수 — 스크롤 시 깜빡임 방지. */
  overscan?: number;
  className?: string;
}

export function VirtualList<T>({
  items,
  estimatedRowHeight,
  threshold = 100,
  renderItem,
  maxHeight = 560,
  overscan = 6,
  className,
}: VirtualListProps<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(maxHeight);

  // 임계값 미만이면 가상 모드 OFF — children 그대로 펼치고 컨테이너 스크롤도 끈다.
  const virtual = items.length >= threshold;

  // viewport 측정 — ResizeObserver 없이 mount 시 1회 + maxHeight 변동 시 반영.
  useEffect(() => {
    if (!virtual) return;
    const el = containerRef.current;
    if (!el) return;
    setViewport(Math.min(el.clientHeight || maxHeight, maxHeight));
  }, [virtual, maxHeight]);

  if (!virtual) {
    return (
      <ul role="list" className={className}>
        {items.map((it, i) => renderItem(it, i))}
      </ul>
    );
  }

  const total = items.length * estimatedRowHeight;
  const startIdx = Math.max(
    0,
    Math.floor(scrollTop / estimatedRowHeight) - overscan,
  );
  const endIdx = Math.min(
    items.length,
    Math.ceil((scrollTop + viewport) / estimatedRowHeight) + overscan,
  );
  const visible = items.slice(startIdx, endIdx);
  const offsetY = startIdx * estimatedRowHeight;

  return (
    <div
      ref={containerRef}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      style={{ maxHeight, overflowY: "auto" }}
      className={className}
      role="region"
      aria-label="가상 스크롤 리스트"
    >
      <div style={{ height: total, position: "relative" }}>
        <ul
          role="list"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            transform: `translateY(${offsetY}px)`,
          }}
        >
          {visible.map((it, i) => renderItem(it, startIdx + i))}
        </ul>
      </div>
    </div>
  );
}
