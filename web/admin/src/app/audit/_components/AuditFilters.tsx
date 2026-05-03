"use client";

/**
 * AuditFilters — 감사 화면 상단의 필터 바.
 *
 * 책임은 *상태 시각화 + 변경 이벤트 발산*에 한정한다. 실제 fetch 트리거는 부모가
 * 상태 변화에 반응해 ``useAdminResource`` path를 갱신하는 방식으로 처리한다.
 *
 * 컨트롤:
 *  - since: ISO date(YYYY-MM-DD) 입력 — 그 날 자정 이후 항목.
 *  - area: 백엔드 영역 키 드롭다운.
 *  - action: 액션 키 드롭다운.
 *  - outcome: 결과 필터(선택) — applied/pending/rejected/dry_run.
 *  - limit: 페이지 크기 — 기본 200.
 *  - reset / CSV 내보내기 / 새로고침.
 *
 * 빈 값은 백엔드로 보내지 않는다 (path builder에서 누락).
 */

import { Download, FilterX, RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";
import { AUDIT_AREAS, AUDIT_ACTIONS } from "./audit-utils";

export interface AuditFilterState {
  since: string;
  area: string;
  action: string;
  outcome: string;
  limit: number;
}

export const DEFAULT_FILTERS: AuditFilterState = {
  since: "",
  area: "",
  action: "",
  outcome: "",
  limit: 200,
};

/** 백엔드가 비워둔 경우만 인덱싱 — 결과는 빈 문자열을 제거한 객체. */
export function buildAuditQuery(state: AuditFilterState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.since) params.set("since", state.since);
  if (state.area) params.set("area", state.area);
  if (state.action) params.set("action", state.action);
  if (state.outcome) params.set("outcome", state.outcome);
  if (state.limit && state.limit > 0) params.set("limit", String(state.limit));
  return params;
}

interface AuditFiltersProps {
  value: AuditFilterState;
  onChange: (next: AuditFilterState) => void;
  onReset: () => void;
  onRefresh: () => void;
  onExportCsv: () => void;
  /** CSV 다운로드 가능 여부 — 항목이 없으면 비활성화. */
  canExport: boolean;
  /** 현재 백엔드 호출 중이면 true — 새로고침 버튼이 회전 표시. */
  isLoading: boolean;
}

export function AuditFilters({
  value,
  onChange,
  onReset,
  onRefresh,
  onExportCsv,
  canExport,
  isLoading,
}: AuditFiltersProps) {
  // 단일 필드 헬퍼 — partial state로 onChange 호출.
  const update = <K extends keyof AuditFilterState>(
    key: K,
    next: AuditFilterState[K],
  ) => onChange({ ...value, [key]: next });

  return (
    <section
      aria-label="감사 필터"
      className="flex flex-wrap items-end gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) px-4 py-3"
    >
      <div className="flex flex-col gap-1">
        <label
          htmlFor="audit-since"
          className="text-xs font-medium text-(--muted-foreground)"
        >
          이후 (날짜)
        </label>
        <Input
          id="audit-since"
          type="date"
          value={value.since}
          onChange={(e) => update("since", e.target.value)}
          containerClassName="w-44"
          className="w-44"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="audit-area"
          className="text-xs font-medium text-(--muted-foreground)"
        >
          영역
        </label>
        <select
          id="audit-area"
          value={value.area}
          onChange={(e) => update("area", e.target.value)}
          className="rounded-(--radius-m) border border-(--border) bg-(--card) px-3 py-2 text-sm text-(--foreground) focus:border-(--primary) focus:outline-none"
        >
          <option value="">전체</option>
          {AUDIT_AREAS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="audit-action"
          className="text-xs font-medium text-(--muted-foreground)"
        >
          액션
        </label>
        <select
          id="audit-action"
          value={value.action}
          onChange={(e) => update("action", e.target.value)}
          className="rounded-(--radius-m) border border-(--border) bg-(--card) px-3 py-2 text-sm text-(--foreground) focus:border-(--primary) focus:outline-none"
        >
          <option value="">전체</option>
          {AUDIT_ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="audit-outcome"
          className="text-xs font-medium text-(--muted-foreground)"
        >
          결과
        </label>
        <select
          id="audit-outcome"
          value={value.outcome}
          onChange={(e) => update("outcome", e.target.value)}
          className="rounded-(--radius-m) border border-(--border) bg-(--card) px-3 py-2 text-sm text-(--foreground) focus:border-(--primary) focus:outline-none"
        >
          <option value="">전체</option>
          <option value="applied">applied</option>
          <option value="pending">pending</option>
          <option value="rejected">rejected</option>
          <option value="dry_run">dry_run</option>
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="audit-limit"
          className="text-xs font-medium text-(--muted-foreground)"
        >
          최대 표시
        </label>
        <Input
          id="audit-limit"
          type="number"
          min={10}
          max={2000}
          step={10}
          value={value.limit}
          onChange={(e) => {
            const n = Number(e.target.value);
            // NaN/음수는 기본값으로 클램프 — 사용자가 지운 직후 잠시 상태를 비워둘 수 있도록 0은 허용.
            update("limit", Number.isFinite(n) && n >= 0 ? n : DEFAULT_FILTERS.limit);
          }}
          containerClassName="w-28"
          className="w-28"
        />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<FilterX size={14} aria-hidden />}
          onClick={onReset}
          aria-label="필터 초기화"
        >
          초기화
        </Button>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<RefreshCw size={14} aria-hidden className={isLoading ? "motion-safe:animate-spin" : undefined} />}
          onClick={onRefresh}
          aria-label="새로고침"
        >
          새로고침
        </Button>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<Download size={14} aria-hidden />}
          onClick={onExportCsv}
          disabled={!canExport}
          aria-label="CSV 내보내기"
        >
          CSV
        </Button>
      </div>
    </section>
  );
}
