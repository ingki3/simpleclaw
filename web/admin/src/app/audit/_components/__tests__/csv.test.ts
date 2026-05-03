/**
 * CSV 직렬화 — 헤더, 콤마/CR/LF/따옴표 이스케이프, before/after JSON 직렬화 검증.
 */

import { describe, expect, test } from "vitest";
import { defaultFilename, toAuditCsv } from "../csv";
import type { AuditEntryDTO } from "../audit-utils";

function entry(overrides: Partial<AuditEntryDTO> = {}): AuditEntryDTO {
  return {
    id: "id-1",
    ts: "2026-05-03T12:00:00Z",
    actor_id: "local",
    trace_id: "trace-abc",
    action: "config.update",
    area: "llm",
    target: "llm.providers.claude.model",
    before: { model: "claude-3" },
    after: { model: "claude-4" },
    outcome: "applied",
    requires_restart: false,
    affected_modules: ["llm"],
    undoable: true,
    reason: null,
    ...overrides,
  };
}

describe("toAuditCsv", () => {
  test("BOM과 헤더 행이 항상 첫 줄에 있다", () => {
    const csv = toAuditCsv([]);
    // BOM은 \uFEFF
    expect(csv.startsWith("\uFEFF")).toBe(true);
    const firstLine = csv.slice(1).split("\r\n")[0];
    expect(firstLine.split(",")).toEqual([
      "id",
      "ts",
      "actor",
      "action",
      "area",
      "target",
      "outcome",
      "undoable",
      "before",
      "after",
      "trace_id",
      "requires_restart",
      "affected_modules",
      "reason",
    ]);
  });

  test("한 행의 객체 before/after는 JSON 문자열로 quote된다", () => {
    const csv = toAuditCsv([entry()]);
    // before/after는 콤마를 포함한 JSON이므로 quote되어야 한다.
    expect(csv).toContain('"{""model"":""claude-3""}"');
    expect(csv).toContain('"{""model"":""claude-4""}"');
  });

  test("affected_modules는 세미콜론 구분 문자열", () => {
    const csv = toAuditCsv([entry({ affected_modules: ["a", "b"] })]);
    expect(csv).toContain("a;b");
  });

  test("따옴표/콤마/개행을 포함한 target은 RFC 4180으로 quote된다", () => {
    const csv = toAuditCsv([
      entry({ target: 'foo,"bar"\n', before: undefined, after: undefined }),
    ]);
    // 콤마와 따옴표 포함 → 전체 셀이 quote, 내부 따옴표 두 배.
    expect(csv).toContain('"foo,""bar""\n"');
  });

  test("reason이 null이면 빈 셀", () => {
    const csv = toAuditCsv([entry({ reason: null })]);
    // 마지막 셀이 빈 문자열로 끝나는지 — 줄 끝 ',' 직후 \r\n 또는 EOF.
    const dataLine = csv.split("\r\n")[1];
    expect(dataLine.endsWith(",")).toBe(true);
  });

  test("여러 항목은 행 단위로 구분된다", () => {
    const csv = toAuditCsv([entry({ id: "a" }), entry({ id: "b" })]);
    const lines = csv.split("\r\n");
    // 헤더 + 2 + 빈 라인 없음.
    expect(lines.length).toBe(3);
    expect(lines[1].split(",")[0]).toBe("a");
    expect(lines[2].split(",")[0]).toBe("b");
  });
});

describe("defaultFilename", () => {
  test("audit-YYYY-MM-DD-HHmm.csv 포맷", () => {
    const name = defaultFilename(new Date("2026-05-03T14:30:00"));
    // 로컬 타임 의존이지만 정규식만 검증하면 환경 독립.
    expect(name).toMatch(/^audit-\d{4}-\d{2}-\d{2}-\d{4}\.csv$/);
  });
});
