/**
 * cron parser + nextRuns 단위 테스트.
 *
 * 본 모듈은 운영자 입력 검증 + DryRun 미리보기의 SSOT 다.
 * 결정성을 확보하기 위해 nextRuns 는 항상 명시적인 `from` 을 받는다.
 */
import { describe, expect, it } from "vitest";

import {
  expandFriendly,
  nextRuns,
  parseCron,
} from "@/app/(shell)/cron/_cron";

describe("parseCron", () => {
  it("표준 5-필드 표현식을 파싱한다", () => {
    const r = parseCron("*/5 * * * *");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.normalized).toBe("*/5 * * * *");
    expect(r.fields[0]).toEqual([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]);
  });

  it("요일 영문 약어를 정수로 매핑한다", () => {
    const r = parseCron("0 9 * * MON");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.fields[4]).toEqual([1]);
  });

  it("`every 2h` 친화 표기를 표준 표현으로 정규화한다", () => {
    const r = parseCron("every 2h");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.normalized).toBe("0 */2 * * *");
  });

  it("`every 5m` 친화 표기를 표준 표현으로 정규화한다", () => {
    const r = parseCron("every 5m");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.normalized).toBe("*/5 * * * *");
  });

  it("필드가 4개면 실패", () => {
    const r = parseCron("0 9 * *");
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.message).toContain("5개 필드");
  });

  it("범위 밖 값은 실패", () => {
    const r = parseCron("99 * * * *");
    expect(r.ok).toBe(false);
  });

  it("범위 시작 > 끝 이면 실패", () => {
    const r = parseCron("0 9-5 * * *");
    expect(r.ok).toBe(false);
  });

  it("콤마/범위/스텝 조합을 파싱한다", () => {
    const r = parseCron("0 0 1,15 * 1-5/2");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    // 1,15 일 / 월~금 중 격일 (1, 3, 5).
    expect(r.fields[2]).toEqual([1, 15]);
    expect(r.fields[4]).toEqual([1, 3, 5]);
  });

  it("dow=7 은 일요일(0) 으로 매핑", () => {
    const r = parseCron("0 0 * * 7");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.fields[4]).toEqual([0]);
  });

  it("빈 입력은 실패", () => {
    const r = parseCron("   ");
    expect(r.ok).toBe(false);
  });
});

describe("expandFriendly", () => {
  it("표준 표현은 그대로 통과", () => {
    expect(expandFriendly("0 0 * * *")).toBe("0 0 * * *");
  });

  it("`every 2h` → `0 */2 * * *`", () => {
    expect(expandFriendly("every 2h")).toBe("0 */2 * * *");
  });

  it("`every 60m` 처럼 단위를 벗어나는 친화 표기는 그대로 둬서 검증이 실패하도록 한다", () => {
    expect(expandFriendly("every 60m")).toBe("every 60m");
  });
});

describe("nextRuns", () => {
  it("매시 정각 표현 — 다음 5회는 정각이 늘어나는 순서", () => {
    const parsed = parseCron("0 * * * *");
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const from = new Date("2026-05-05T10:30:00");
    const runs = nextRuns(parsed, from, 5);
    expect(runs).toHaveLength(5);
    expect(runs[0]?.getHours()).toBe(11);
    expect(runs[0]?.getMinutes()).toBe(0);
    expect(runs[4]?.getHours()).toBe(15);
  });

  it("매일 09:00 — 다음 3회는 다음 09시들", () => {
    const parsed = parseCron("0 9 * * *");
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const from = new Date("2026-05-05T10:00:00");
    const runs = nextRuns(parsed, from, 3);
    expect(runs).toHaveLength(3);
    expect(runs[0]?.getDate()).toBe(6);
    expect(runs[0]?.getHours()).toBe(9);
    expect(runs[2]?.getDate()).toBe(8);
  });

  it("count=0 이면 빈 배열", () => {
    const parsed = parseCron("0 * * * *");
    if (!parsed.ok) throw new Error("parse should ok");
    expect(nextRuns(parsed, new Date(), 0)).toEqual([]);
  });
});
