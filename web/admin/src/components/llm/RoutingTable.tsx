"use client";

/**
 * RoutingTable — 작업 카테고리별 모델 매핑 편집기.
 *
 * admin.pen Screen 02 / BIZ-45 §범위 — 카테고리(general/coding/reasoning/tools)
 * 각각에 대해 활성 프로바이더 중 하나를 드롭다운으로 고른다. 미설정(null)은
 * 라우터가 ``llm.default``로 폴백한다.
 */

import { ROUTING_CATEGORIES, type RoutingCategory, type RoutingMap } from "@/lib/api/llm";
import { Badge } from "@/components/atoms/Badge";
import { cn } from "@/lib/cn";

export interface RoutingTableProps {
  value: RoutingMap;
  /** 드롭다운에 노출할 후보 — 활성/비활성 무관하게 ``llm.providers``의 키. */
  providers: string[];
  /** ``llm.default`` — 카테고리 미설정 시 폴백 라벨용. */
  fallback?: string;
  onChange: (next: RoutingMap) => void;
  className?: string;
}

const CATEGORY_LABEL: Record<RoutingCategory, string> = {
  general: "일반",
  coding: "코딩",
  reasoning: "추론",
  tools: "도구 호출",
};

const CATEGORY_HINT: Record<RoutingCategory, string> = {
  general: "기본 채팅·요약·번역 등 일상 호출.",
  coding: "코드 생성·리뷰·리팩토링.",
  reasoning: "복잡한 다단 추론·계획 수립.",
  tools: "Native function calling·도구 사용 흐름.",
};

export function RoutingTable({
  value,
  providers,
  fallback,
  onChange,
  className,
}: RoutingTableProps) {
  function update(cat: RoutingCategory, next: string) {
    const merged: RoutingMap = { ...value };
    if (!next) {
      delete merged[cat];
    } else {
      merged[cat] = next;
    }
    onChange(merged);
  }

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <header className="flex items-center justify-between">
        <span className="text-sm font-medium text-[--foreground-strong]">
          카테고리 라우팅
        </span>
        <Badge tone="success">↻ Hot</Badge>
      </header>
      <p className="text-xs text-[--muted-foreground]">
        호출 분류에 따라 사용할 프로바이더를 선택하세요. 비워 두면{" "}
        <code className="font-mono text-[--foreground]">{fallback ?? "default"}</code>{" "}
        로 폴백합니다.
      </p>
      <div className="overflow-hidden rounded-[--radius-m] border border-[--border]">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-[--surface] text-xs uppercase text-[--muted-foreground]">
            <tr>
              <th className="px-3 py-2 text-left font-medium">카테고리</th>
              <th className="px-3 py-2 text-left font-medium">설명</th>
              <th className="px-3 py-2 text-left font-medium">프로바이더</th>
            </tr>
          </thead>
          <tbody>
            {ROUTING_CATEGORIES.map((cat) => {
              const current = value[cat] ?? "";
              return (
                <tr key={cat} className="border-t border-[--border]">
                  <td className="px-3 py-2 font-medium text-[--foreground-strong]">
                    {CATEGORY_LABEL[cat]}
                    <span className="ml-1 font-mono text-[10px] text-[--muted-foreground]">
                      {cat}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-[--muted-foreground]">
                    {CATEGORY_HINT[cat]}
                  </td>
                  <td className="px-3 py-2">
                    <select
                      aria-label={`${CATEGORY_LABEL[cat]} 카테고리 프로바이더`}
                      value={current}
                      onChange={(e) => update(cat, e.target.value)}
                      className="w-full rounded-[--radius-m] border border-[--border] bg-[--card] px-3 py-1.5 text-sm text-[--foreground] focus:border-[--primary] focus:outline-none"
                    >
                      <option value="">— (default 사용)</option>
                      {providers.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
