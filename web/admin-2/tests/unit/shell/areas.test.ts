/**
 * areas.ts SSOT 단위 테스트 — 11개 영역과 검색·매칭 헬퍼.
 */
import { describe, expect, it } from "vitest";
import { AREAS, findAreaByPath, searchAreas } from "@/app/areas";

describe("AREAS SSOT", () => {
  it("정확히 11개 영역을 정의한다", () => {
    expect(AREAS).toHaveLength(11);
  });

  it("이슈 명세에 적힌 11개 path 를 모두 갖는다", () => {
    const expected = [
      "/dashboard",
      "/llm-router",
      "/persona",
      "/skills-recipes",
      "/cron",
      "/memory",
      "/secrets",
      "/channels",
      "/logging",
      "/audit",
      "/system",
    ];
    expect(AREAS.map((a) => a.path)).toEqual(expected);
  });

  it("path 중복이 없다", () => {
    const set = new Set(AREAS.map((a) => a.path));
    expect(set.size).toBe(AREAS.length);
  });

  it("모든 영역이 label, description, icon, keywords 를 갖는다", () => {
    for (const a of AREAS) {
      expect(a.label.length).toBeGreaterThan(0);
      expect(a.description.length).toBeGreaterThan(0);
      expect(a.icon.length).toBeGreaterThan(0);
      expect(a.keywords.length).toBeGreaterThan(0);
    }
  });
});

describe("findAreaByPath", () => {
  it("정확 매칭", () => {
    expect(findAreaByPath("/cron")?.label).toBe("크론");
  });

  it("하위 경로도 매칭(예: /memory/clusters)", () => {
    expect(findAreaByPath("/memory/clusters")?.path).toBe("/memory");
  });

  it("등록되지 않은 경로는 null", () => {
    expect(findAreaByPath("/unknown-area")).toBeNull();
  });

  it("빈 문자열은 null", () => {
    expect(findAreaByPath("")).toBeNull();
  });
});

describe("searchAreas", () => {
  it("빈 쿼리는 전체 반환", () => {
    expect(searchAreas("")).toHaveLength(11);
    expect(searchAreas("   ")).toHaveLength(11);
  });

  it("label 부분일치 (대소문자 무시)", () => {
    const result = searchAreas("크론");
    expect(result.map((a) => a.path)).toContain("/cron");
  });

  it("path 부분일치", () => {
    const result = searchAreas("llm");
    expect(result.map((a) => a.path)).toContain("/llm-router");
  });

  it("keyword 부분일치 (영문 alias)", () => {
    const result = searchAreas("schedule");
    expect(result.map((a) => a.path)).toContain("/cron");
  });

  it("매칭 0건이면 빈 배열", () => {
    expect(searchAreas("zzz-no-match-xyz")).toHaveLength(0);
  });
});
