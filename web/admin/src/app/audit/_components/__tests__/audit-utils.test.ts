/**
 * audit-utils — 시간/undo 윈도/페이로드 직렬화 단위 검증.
 *
 * 시각 의존 함수는 ``now`` 인자를 명시 주입해 deterministic.
 */

import { describe, expect, test } from "vitest";
import {
  buildAuditQuery as _build, // 별도 파일에서 export — placeholder
} from "../AuditFilters";
import {
  formatPayload,
  formatPayloadInline,
  isUndoableNow,
  outcomeTone,
  parseTs,
  remainingWindowLabel,
  UNDO_WINDOW_MS,
  type AuditEntryDTO,
} from "../audit-utils";

const FROZEN_NOW = Date.parse("2026-05-03T12:00:00Z");
const oneMinuteAgo = new Date(FROZEN_NOW - 60_000).toISOString();
const sixMinutesAgo = new Date(FROZEN_NOW - 6 * 60_000).toISOString();

function entry(partial: Partial<AuditEntryDTO> = {}): AuditEntryDTO {
  return {
    id: "a-1",
    ts: oneMinuteAgo,
    actor_id: "local",
    trace_id: "",
    action: "config.update",
    area: "llm",
    target: "llm.providers.claude.model",
    before: { model: "claude-3" },
    after: { model: "claude-4" },
    outcome: "applied",
    requires_restart: false,
    affected_modules: [],
    undoable: true,
    ...partial,
  };
}

describe("parseTs", () => {
  test("ISO 문자열을 ms로 변환", () => {
    expect(parseTs("2026-05-03T12:00:00Z")).toBe(FROZEN_NOW);
  });
  test("epoch seconds는 자동으로 ms로 보정", () => {
    expect(parseTs(1_700_000_000)).toBe(1_700_000_000_000);
  });
  test("epoch ms는 그대로", () => {
    expect(parseTs(1_700_000_000_000)).toBe(1_700_000_000_000);
  });
  test("잘못된 입력은 null", () => {
    expect(parseTs("not-a-date")).toBeNull();
    expect(parseTs(null)).toBeNull();
    expect(parseTs(undefined)).toBeNull();
  });
});

describe("isUndoableNow", () => {
  test("undoable=true이고 5분 이내 + applied면 true", () => {
    expect(isUndoableNow(entry({ ts: oneMinuteAgo }), FROZEN_NOW)).toBe(true);
  });
  test("undoable=false면 false", () => {
    expect(isUndoableNow(entry({ undoable: false }), FROZEN_NOW)).toBe(false);
  });
  test("outcome=rejected면 false", () => {
    expect(isUndoableNow(entry({ outcome: "rejected" }), FROZEN_NOW)).toBe(false);
  });
  test("5분을 초과하면 false (윈도 외)", () => {
    expect(isUndoableNow(entry({ ts: sixMinutesAgo }), FROZEN_NOW)).toBe(false);
  });
  test("정확히 5분 경계는 true(<=)", () => {
    const ts = new Date(FROZEN_NOW - UNDO_WINDOW_MS).toISOString();
    expect(isUndoableNow(entry({ ts }), FROZEN_NOW)).toBe(true);
  });
  test("ts 파싱 실패면 false (안전 폴백)", () => {
    expect(isUndoableNow(entry({ ts: "garbage" }), FROZEN_NOW)).toBe(false);
  });
});

describe("remainingWindowLabel", () => {
  test("윈도 안이면 mm:ss", () => {
    // 1분 경과 → 4분 남음
    expect(remainingWindowLabel(entry({ ts: oneMinuteAgo }), FROZEN_NOW)).toBe(
      "4:00",
    );
  });
  test("윈도 외면 '—'", () => {
    expect(remainingWindowLabel(entry({ ts: sixMinutesAgo }), FROZEN_NOW)).toBe(
      "—",
    );
  });
});

describe("outcomeTone", () => {
  test("표준 결과는 명시 매핑", () => {
    expect(outcomeTone("applied")).toBe("success");
    expect(outcomeTone("pending")).toBe("info");
    expect(outcomeTone("rejected")).toBe("warning");
    expect(outcomeTone("failed")).toBe("error");
  });
  test("미지정 결과는 neutral", () => {
    expect(outcomeTone("strange-thing")).toBe("neutral");
  });
});

describe("formatPayload", () => {
  test("객체는 JSON pretty", () => {
    expect(formatPayload({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
  test("string은 그대로", () => {
    expect(formatPayload("hello")).toBe("hello");
  });
  test("null/undefined는 dash", () => {
    expect(formatPayload(null)).toBe("—");
    expect(formatPayload(undefined)).toBe("—");
  });
});

describe("formatPayloadInline", () => {
  test("길면 truncate", () => {
    const big = { value: "x".repeat(100) };
    const out = formatPayloadInline(big);
    expect(out?.endsWith("…")).toBe(true);
    expect(out?.length).toBe(78);
  });
  test("짧은 객체는 한 줄 JSON", () => {
    expect(formatPayloadInline({ a: 1 })).toBe('{"a":1}');
  });
  test("undefined는 undefined", () => {
    expect(formatPayloadInline(undefined)).toBeUndefined();
  });
});

// 별도 파일이지만 같은 묶음에서 검증 — 빈 값은 query에 반영하지 않는다.
describe("buildAuditQuery", () => {
  test("기본값(빈 since/area/action/outcome)은 limit만 포함", () => {
    const q = _build({
      since: "",
      area: "",
      action: "",
      outcome: "",
      limit: 200,
    });
    expect(q.toString()).toBe("limit=200");
  });
  test("필터가 채워지면 모두 인코딩", () => {
    const q = _build({
      since: "2026-05-01",
      area: "llm",
      action: "config.update",
      outcome: "applied",
      limit: 50,
    });
    expect(q.get("since")).toBe("2026-05-01");
    expect(q.get("area")).toBe("llm");
    expect(q.get("action")).toBe("config.update");
    expect(q.get("outcome")).toBe("applied");
    expect(q.get("limit")).toBe("50");
  });
  test("limit=0이면 limit은 누락 (기본 백엔드 사용)", () => {
    const q = _build({
      since: "",
      area: "",
      action: "",
      outcome: "",
      limit: 0,
    });
    expect(q.has("limit")).toBe(false);
  });
});
