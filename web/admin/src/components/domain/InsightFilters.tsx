"use client";

/**
 * InsightFilters — BIZ-92 / BIZ-90 §필터 행.
 *
 * 컨트롤된 필터 폼. 모든 상태는 부모가 관리하며, 변경 시 콜백으로 전달한다.
 *
 * 슬롯:
 *  1. 토픽 검색 (text)
 *  2. Confidence ≥ slider (0..1, 0.05 step)
 *  3. Source 채널 multi-select (chip toggle)
 *  4. "cron/recipe 노이즈 자동 다운(0.3×)" 정보 칩 — 정책 안내 only.
 *
 * 정책 칩의 0.3× 는 BIZ-90 §"자동 다운가중" 결정값. 실제 가중은 백엔드가 처리하고
 * UI 는 운영자가 그 사실을 알아챌 수 있도록 한 줄로만 노출한다.
 */

import { Info, Search } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Input } from "@/components/atoms/Input";
import { cn } from "@/lib/cn";

export interface InsightFilterValue {
  /** 부분 일치 검색 — 빈 문자열은 비활성. */
  query: string;
  /** 0..1. 이 값 이상의 confidence 만 통과. 0 이면 비활성과 동치. */
  minConfidence: number;
  /** 활성 채널 ids — 비어있으면 "전체" 의미 (필터링 안 함). */
  channels: string[];
}

export interface InsightFiltersProps {
  value: InsightFilterValue;
  onChange: (next: InsightFilterValue) => void;
  /** 현재 데이터셋에서 발견된 채널 목록. 비어있으면 채널 칩 영역을 숨긴다. */
  availableChannels: string[];
  /** confidence slider 와 cron-noise 칩을 숨길 때(예: 블록리스트 탭). */
  hideConfidence?: boolean;
  hideNoiseHint?: boolean;
  className?: string;
}

export function InsightFilters({
  value,
  onChange,
  availableChannels,
  hideConfidence,
  hideNoiseHint,
  className,
}: InsightFiltersProps) {
  const toggleChannel = (id: string) => {
    const next = value.channels.includes(id)
      ? value.channels.filter((c) => c !== id)
      : [...value.channels, id];
    onChange({ ...value, channels: next });
  };

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-(--radius-m) border border-(--border) bg-(--card) px-3 py-2.5",
        className,
      )}
    >
      {/* 1. 토픽 검색 */}
      <label className="flex min-w-[200px] flex-1 items-center gap-2 text-xs">
        <span className="sr-only">토픽 검색</span>
        <Input
          type="search"
          value={value.query}
          onChange={(e) => onChange({ ...value, query: e.target.value })}
          placeholder="토픽·본문 검색"
          aria-label="토픽 또는 본문 검색"
          leftIcon={<Search size={12} aria-hidden />}
        />
      </label>

      {/* 2. Confidence ≥ slider */}
      {!hideConfidence ? (
        <label className="flex min-w-[180px] items-center gap-2 text-xs text-(--muted-foreground)">
          <span className="whitespace-nowrap">Confidence ≥</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={value.minConfidence}
            onChange={(e) =>
              onChange({ ...value, minConfidence: Number(e.target.value) })
            }
            aria-label="최소 신뢰도"
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={value.minConfidence}
            className="h-1.5 w-32 cursor-pointer accent-(--primary)"
          />
          <span className="w-10 font-mono tabular-nums text-(--foreground)">
            {value.minConfidence.toFixed(2)}
          </span>
        </label>
      ) : null}

      {/* 3. Source 채널 multi-select */}
      {availableChannels.length > 0 ? (
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Source 채널</legend>
          <span className="text-xs text-(--muted-foreground)">Source</span>
          {availableChannels.map((channel) => {
            const active = value.channels.includes(channel);
            return (
              <button
                key={channel}
                type="button"
                role="checkbox"
                aria-checked={active}
                onClick={() => toggleChannel(channel)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-(--radius-pill) border px-2 py-0.5 text-xs font-medium transition-colors",
                  active
                    ? "border-(--primary) bg-(--primary-tint) text-(--primary)"
                    : "border-(--border) bg-(--surface) text-(--muted-foreground) hover:text-(--foreground)",
                )}
              >
                {channel}
              </button>
            );
          })}
          {value.channels.length > 0 ? (
            <button
              type="button"
              onClick={() => onChange({ ...value, channels: [] })}
              className="text-[10px] text-(--muted-foreground) hover:underline"
              aria-label="채널 필터 초기화"
            >
              초기화
            </button>
          ) : null}
        </fieldset>
      ) : null}

      {/* 4. cron/recipe 노이즈 자동 다운가중 정보 칩 */}
      {!hideNoiseHint ? (
        <span className="ml-auto inline-flex items-center gap-1.5 rounded-(--radius-pill) border border-transparent bg-(--color-info-bg) px-2 py-0.5">
          <Info size={11} aria-hidden className="text-(--color-info)" />
          <span className="text-[11px] text-(--color-info)">
            cron/recipe 노이즈 자동 다운(0.3×)
          </span>
          <Badge tone="info" className="!text-[10px]">
            정책
          </Badge>
        </span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// helper — InsightFilterValue 의 기본값.
// ---------------------------------------------------------------------------

export const DEFAULT_INSIGHT_FILTER: InsightFilterValue = {
  query: "",
  minConfidence: 0,
  channels: [],
};
