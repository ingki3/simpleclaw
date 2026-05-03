/**
 * Audit CSV 직렬화 — 필터 적용 후 화면에 보이는 항목을 그대로 내보낸다.
 *
 * 정책:
 *  - 한 행 = 한 항목. 컬럼은 ``ts, actor, action, area, target, outcome, undoable, before, after, trace_id, requires_restart, affected_modules, reason``.
 *  - before/after는 JSON 문자열로 직렬화 — 마스킹은 백엔드가 이미 적용했음을 신뢰.
 *  - RFC 4180 기본 따옴표 이스케이프 — 큰따옴표는 ``""``로, CR/LF/콤마/따옴표를 포함하면 quote.
 *  - 첫 행은 헤더. UTF-8 BOM을 prepend해 Excel/한글 호환을 보장한다.
 *  - 대용량 대비는 하지 않는다 — admin은 단일 운영자이고 보통 항목 수백 건 이하.
 */

import type { AuditEntryDTO } from "./audit-utils";

const COLUMNS: ReadonlyArray<{
  header: string;
  pick: (e: AuditEntryDTO) => string | number | boolean | undefined | null;
}> = [
  { header: "id", pick: (e) => e.id },
  { header: "ts", pick: (e) => e.ts },
  { header: "actor", pick: (e) => e.actor_id },
  { header: "action", pick: (e) => e.action },
  { header: "area", pick: (e) => e.area },
  { header: "target", pick: (e) => e.target },
  { header: "outcome", pick: (e) => e.outcome },
  { header: "undoable", pick: (e) => e.undoable },
  { header: "before", pick: (e) => stringifyValue(e.before) },
  { header: "after", pick: (e) => stringifyValue(e.after) },
  { header: "trace_id", pick: (e) => e.trace_id },
  { header: "requires_restart", pick: (e) => e.requires_restart },
  {
    header: "affected_modules",
    pick: (e) => (e.affected_modules ?? []).join(";"),
  },
  { header: "reason", pick: (e) => e.reason ?? "" },
];

function stringifyValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** 한 셀을 RFC 4180 규칙으로 quote. 항상 string을 반환한다. */
function escapeCell(value: string | number | boolean | undefined | null): string {
  if (value === undefined || value === null) return "";
  const str = String(value);
  if (/[",\r\n]/.test(str)) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/** 항목 배열을 CSV 텍스트로 직렬화. BOM prepend 포함. */
export function toAuditCsv(entries: ReadonlyArray<AuditEntryDTO>): string {
  const header = COLUMNS.map((c) => escapeCell(c.header)).join(",");
  const rows = entries.map((e) =>
    COLUMNS.map((c) => escapeCell(c.pick(e))).join(","),
  );
  return "\uFEFF" + [header, ...rows].join("\r\n");
}

/**
 * CSV를 `Blob`으로 변환해 같은 origin에서 다운로드 트리거.
 *
 * Storybook/SSR 환경에서는 ``document``가 없으므로 호출 측에서 가드한다.
 */
export function downloadAuditCsv(
  entries: ReadonlyArray<AuditEntryDTO>,
  filename: string,
): void {
  const text = toAuditCsv(entries);
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 비동기 IO 종료 후 URL 폐기 — 즉시 revoke하면 일부 브라우저가 다운로드를 취소.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** ``audit-2026-05-03-1430.csv`` 같은 안정적 파일명. */
export function defaultFilename(now: Date = new Date()): string {
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const mi = String(now.getMinutes()).padStart(2, "0");
  return `audit-${yyyy}-${mm}-${dd}-${hh}${mi}.csv`;
}
