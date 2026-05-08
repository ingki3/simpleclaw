/**
 * Audit `_data` 모듈의 순수 함수 단위 테스트.
 *
 * applyAuditFilter / canUndo / listActors / findEntryById / getAuditSnapshot 의
 * 결정적 동작을 보장 — fixture 변경에 강하게 의존하지 않도록 sample 셋만 사용한다.
 */
import { describe, expect, it } from "vitest";
import {
  applyAuditFilter,
  canUndo,
  findEntryById,
  getAuditSnapshot,
  listActors,
  timeRangeMinutes,
} from "@/app/(shell)/audit/_data";
import {
  ENTRIES_SAMPLE,
  ENT_APPLIED_LLM,
  ENT_APPLIED_SECRET,
  ENT_BY_AGENT,
  ENT_FAILED,
  ENT_OLD,
  ENT_PENDING,
  ENT_ROLLED_BACK,
} from "./_fixture";

const NOW = Date.parse("2026-05-05T14:32:31.000Z");

const BASE_FILTER = {
  query: "",
  area: "all",
  action: "all",
  actor: "all",
  range: "30d",
  failedOnly: false,
  now: NOW,
} as const;

describe("audit/_data — applyAuditFilter", () => {
  it("기본 필터 — 30d 안의 entry 만, 최신순 정렬", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, BASE_FILTER);
    // ENT_OLD 는 60일 이전이라 제외, 나머지 6건 남음.
    expect(out).toHaveLength(6);
    expect(out.map((e) => e.id)).not.toContain(ENT_OLD.id);
    // 최신 → 옛날 순.
    for (let i = 1; i < out.length; i += 1) {
      expect(Date.parse(out[i - 1].timestamp)).toBeGreaterThanOrEqual(
        Date.parse(out[i].timestamp),
      );
    }
  });

  it("range='all' 이면 ENT_OLD 도 포함된다", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      range: "all",
    });
    expect(out.map((e) => e.id)).toContain(ENT_OLD.id);
  });

  it("area 필터 — 'secrets' 만 통과", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      area: "secrets",
    });
    expect(out.every((e) => e.area === "secrets")).toBe(true);
    expect(out.map((e) => e.id)).toEqual(
      expect.arrayContaining([ENT_APPLIED_SECRET.id, ENT_PENDING.id]),
    );
  });

  it("action 필터 — 'config.update' 만 통과", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      action: "config.update",
    });
    expect(out.every((e) => e.action === "config.update")).toBe(true);
    expect(out.map((e) => e.id)).toContain(ENT_APPLIED_LLM.id);
    expect(out.map((e) => e.id)).toContain(ENT_FAILED.id);
  });

  it("actor 필터 — 'DesignAgent' 만 통과", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      actor: "DesignAgent",
    });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(ENT_BY_AGENT.id);
  });

  it("failedOnly — failed 만 통과", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      failedOnly: true,
    });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(ENT_FAILED.id);
  });

  it("query — actor/target/before/after 부분 일치 (대소문자 무시)", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      query: "OPENAI",
    });
    // ENT_APPLIED_SECRET 의 target=secrets/openai_key 가 매칭.
    expect(out.map((e) => e.id)).toContain(ENT_APPLIED_SECRET.id);
  });

  it("query — DesignAgent 액터명도 매칭", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      query: "designagent",
    });
    expect(out.map((e) => e.id)).toContain(ENT_BY_AGENT.id);
  });

  it("range='24h' — 24시간 이내 entry 만", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      range: "24h",
    });
    // NOW=05-05T14:32 기준 24h 이내: 05-05T14:32 (LLM), 05-05T13:08 (secret),
    // 05-05T11:55 (DesignAgent), 05-04T15:30 (rolled-back, NOW-24h 이후).
    // ENT_FAILED(05-02), ENT_PENDING(04-29), ENT_OLD(03-01) 은 모두 하루 이상 이전.
    expect(out.map((e) => e.id).sort()).toEqual(
      [
        ENT_APPLIED_LLM.id,
        ENT_APPLIED_SECRET.id,
        ENT_BY_AGENT.id,
        ENT_ROLLED_BACK.id,
      ].sort(),
    );
  });

  it("AND 결합 — failedOnly + area=llm-router 면 ENT_FAILED 만", () => {
    const out = applyAuditFilter(ENTRIES_SAMPLE, {
      ...BASE_FILTER,
      failedOnly: true,
      area: "llm-router",
      range: "all",
    });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(ENT_FAILED.id);
  });
});

describe("audit/_data — helpers", () => {
  it("canUndo — applied 는 true, 나머지는 false", () => {
    expect(canUndo(ENT_APPLIED_LLM)).toBe(true);
    expect(canUndo(ENT_FAILED)).toBe(false);
    expect(canUndo(ENT_ROLLED_BACK)).toBe(false);
    expect(canUndo(ENT_PENDING)).toBe(false);
  });

  it("listActors — 정렬된 distinct actor", () => {
    const actors = listActors(ENTRIES_SAMPLE);
    expect(actors).toEqual(["DesignAgent", "ingki3"]);
  });

  it("findEntryById — 존재 시 entry, 없으면 null", () => {
    const found = findEntryById(ENTRIES_SAMPLE, ENT_APPLIED_LLM.id);
    expect(found?.id).toBe(ENT_APPLIED_LLM.id);
    expect(findEntryById(ENTRIES_SAMPLE, "missing")).toBeNull();
  });

  it("timeRangeMinutes — 단조 증가 (24h < 7d < 30d < 90d < all)", () => {
    expect(timeRangeMinutes("24h")).toBeLessThan(timeRangeMinutes("7d"));
    expect(timeRangeMinutes("7d")).toBeLessThan(timeRangeMinutes("30d"));
    expect(timeRangeMinutes("30d")).toBeLessThan(timeRangeMinutes("90d"));
    expect(timeRangeMinutes("90d")).toBeLessThan(timeRangeMinutes("all"));
  });

  it("getAuditSnapshot — 픽스처 entries 를 노출", () => {
    const snap = getAuditSnapshot();
    expect(Array.isArray(snap.entries)).toBe(true);
    expect(snap.entries.length).toBeGreaterThan(0);
  });
});
