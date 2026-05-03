"use client";

/**
 * LogFilters — 로그 페이지 상단 필터 바.
 *
 * 구성: [레벨 토글 그룹] [모듈 입력] [검색 입력] [자동 새로고침 스위치] [수동 새로고침]
 *
 * 페이지가 URL 쿼리스트링을 단일 진실원으로 삼기 때문에 본 컴포넌트는 controlled
 * 입력만 받는다. 디바운스/타이핑 처리는 페이지 측에서 결정한다.
 */

import { RefreshCw, Search } from "lucide-react";
import { Badge } from "@/components/atoms/Badge";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { Switch } from "@/components/atoms/Switch";
import { cn } from "@/lib/cn";
import { LEVEL_TOKENS, type LogLevelToken } from "@/lib/api/logs";

const LEVEL_LABEL: Record<LogLevelToken, string> = {
  debug: "debug",
  info: "info",
  warn: "warn",
  error: "error",
};

export interface LogFiltersProps {
  level: LogLevelToken | undefined;
  onLevelChange: (next: LogLevelToken | undefined) => void;
  module: string;
  onModuleChange: (next: string) => void;
  search: string;
  onSearchChange: (next: string) => void;
  autoRefresh: boolean;
  onAutoRefreshChange: (next: boolean) => void;
  onRefreshNow: () => void;
  isRefreshing?: boolean;
}

export function LogFilters({
  level,
  onLevelChange,
  module,
  onModuleChange,
  search,
  onSearchChange,
  autoRefresh,
  onAutoRefreshChange,
  onRefreshNow,
  isRefreshing,
}: LogFiltersProps) {
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-(--radius-m) border border-(--border) bg-(--card) p-3"
      role="search"
      aria-label="로그 필터"
    >
      <div
        className="flex items-center gap-1"
        role="group"
        aria-label="레벨 필터"
      >
        <button
          type="button"
          onClick={() => onLevelChange(undefined)}
          className={cn(
            "rounded-(--radius-sm) border px-2 py-1 text-xs",
            level === undefined
              ? "border-(--primary) bg-(--primary-tint) text-(--primary)"
              : "border-(--border) text-(--muted-foreground) hover:bg-(--surface)",
          )}
          aria-pressed={level === undefined}
        >
          all
        </button>
        {LEVEL_TOKENS.map((tk) => (
          <button
            key={tk}
            type="button"
            onClick={() => onLevelChange(level === tk ? undefined : tk)}
            className={cn(
              "rounded-(--radius-sm) border px-2 py-1 text-xs",
              level === tk
                ? "border-(--primary) bg-(--primary-tint) text-(--primary)"
                : "border-(--border) text-(--muted-foreground) hover:bg-(--surface)",
            )}
            aria-pressed={level === tk}
          >
            {LEVEL_LABEL[tk]}
          </button>
        ))}
      </div>

      <Input
        value={module}
        onChange={(e) => onModuleChange(e.target.value)}
        placeholder="모듈 / action_type"
        aria-label="모듈 필터"
        containerClassName="w-[220px]"
      />

      <Input
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="요약·trace·details 자유 검색"
        aria-label="자유 텍스트 검색"
        leftIcon={<Search size={14} aria-hidden />}
        containerClassName="w-[280px]"
      />

      <div className="ml-auto flex items-center gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-(--muted-foreground)">
          <Switch
            checked={autoRefresh}
            onCheckedChange={onAutoRefreshChange}
            label="자동 새로고침"
          />
          <span>자동 새로고침 (1s)</span>
          {autoRefresh && isRefreshing ? (
            <Badge tone="info" className="ml-1">
              live
            </Badge>
          ) : null}
        </label>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRefreshNow}
          aria-label="지금 새로고침"
        >
          <RefreshCw
            size={14}
            aria-hidden
            className={isRefreshing ? "animate-spin" : undefined}
          />
        </Button>
      </div>
    </div>
  );
}
