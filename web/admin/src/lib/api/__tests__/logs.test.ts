/**
 * Logs API 헬퍼 단위 테스트 — 백엔드 호출 없이 변환 함수만 검증.
 *
 * 백엔드 응답 envelope/필드 보존은 ``client.test.ts``가 담당하므로 본 파일은
 * URL 빌더, 레벨 토큰 매핑, 자유 검색 매칭, 안정 키 생성에 집중한다.
 */

import { describe, expect, test } from "vitest";
import {
  buildLogsPath,
  entryKey,
  entryMatchesSearch,
  LEVEL_API_TO_TOKEN,
  LEVEL_TOKEN_TO_API,
  normalizeLevel,
  parseLevelToken,
  type LogApiEntry,
} from "@/lib/api/logs";

describe("buildLogsPath", () => {
  test("limit만 있으면 다른 키는 추가하지 않는다", () => {
    expect(buildLogsPath({ limit: 50 })).toBe("/admin/v1/logs?limit=50");
  });

  test("UI 토큰을 백엔드 정식 값으로 매핑한다", () => {
    const path = buildLogsPath({ limit: 100, level: "warn" });
    expect(path).toContain("level=WARNING");
    // 'warn'이 아닌 정식 'WARNING'이 그대로 들어간다.
    expect(path).not.toContain("level=warn");
  });

  test("trace_id / module 조합도 모두 직렬화한다", () => {
    const path = buildLogsPath({
      limit: 200,
      traceId: "abc",
      module: "skill",
      level: "error",
    });
    const params = new URLSearchParams(path.split("?")[1]);
    expect(params.get("limit")).toBe("200");
    expect(params.get("trace_id")).toBe("abc");
    expect(params.get("module")).toBe("skill");
    expect(params.get("level")).toBe("ERROR");
  });
});

describe("normalizeLevel / parseLevelToken", () => {
  test("대소문자/약어를 흡수한다", () => {
    expect(normalizeLevel("info")).toBe("INFO");
    expect(normalizeLevel("WARN")).toBe("WARNING");
    expect(normalizeLevel("warning")).toBe("WARNING");
    expect(normalizeLevel("error")).toBe("ERROR");
    expect(normalizeLevel("nonsense")).toBeUndefined();
    expect(normalizeLevel(undefined)).toBeUndefined();
  });

  test("URL 토큰은 화이트리스트만 허용한다", () => {
    expect(parseLevelToken("debug")).toBe("debug");
    expect(parseLevelToken("WARN")).toBe("warn");
    expect(parseLevelToken("trace")).toBeUndefined();
    expect(parseLevelToken(null)).toBeUndefined();
  });

  test("토큰↔API 매핑이 양방향으로 일관적이다", () => {
    for (const [token, api] of Object.entries(LEVEL_TOKEN_TO_API)) {
      expect(LEVEL_API_TO_TOKEN[api]).toBe(token);
    }
  });
});

describe("entryMatchesSearch", () => {
  const entry: LogApiEntry = {
    timestamp: "2026-05-03T01:02:03.000",
    level: "INFO",
    action_type: "skill_execution",
    input_summary: "안녕하세요 — 검색 가능한 한글",
    output_summary: "ok",
    status: "success",
    trace_id: "abcdef0123",
    details: { skill: "telegram", duration: 12 },
  };

  test("빈 needle은 모두 통과", () => {
    expect(entryMatchesSearch(entry, "")).toBe(true);
  });
  test("action_type substring 대소문자 무시", () => {
    expect(entryMatchesSearch(entry, "SKILL_EXEC")).toBe(true);
  });
  test("input_summary 한글 매칭", () => {
    expect(entryMatchesSearch(entry, "안녕")).toBe(true);
  });
  test("trace_id 부분 매칭", () => {
    expect(entryMatchesSearch(entry, "abcdef")).toBe(true);
  });
  test("details JSON 매칭", () => {
    expect(entryMatchesSearch(entry, "telegram")).toBe(true);
  });
  test("매칭 안 되면 false", () => {
    expect(entryMatchesSearch(entry, "zzz")).toBe(false);
  });
});

describe("entryKey", () => {
  test("timestamp + trace_id + action_type가 같으면 동일 키", () => {
    const a: LogApiEntry = {
      timestamp: "t1",
      trace_id: "tr",
      action_type: "x",
    };
    const b: LogApiEntry = {
      timestamp: "t1",
      trace_id: "tr",
      action_type: "x",
    };
    expect(entryKey(a)).toBe(entryKey(b));
  });
  test("timestamp만 다르면 다른 키", () => {
    expect(
      entryKey({ timestamp: "t1", trace_id: "tr", action_type: "x" }),
    ).not.toBe(entryKey({ timestamp: "t2", trace_id: "tr", action_type: "x" }));
  });
  test("필수 필드가 비어 있으면 fallback 인덱스 사용", () => {
    expect(entryKey({}, 7)).toBe("idx-7");
  });
});
