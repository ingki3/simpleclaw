/**
 * ActiveProjectsPanel — 순수 helper(formatRelative) 단위 테스트.
 *
 * 컴포넌트 렌더 자체는 jsdom + msw 가 필요해 비용이 크고, 본 파일은 시간 의존
 * 포매팅 로직만 검증한다 (BIZ-96 — "updated Nh|Nd ago" 표기).
 */

import { describe, expect, it } from "vitest";
import { formatRelative } from "../ActiveProjectsPanel";

describe("formatRelative", () => {
  const now = new Date("2026-05-05T12:00:00Z").getTime();

  it("1분 미만은 '방금'", () => {
    expect(formatRelative(new Date(now - 30_000).toISOString(), now)).toBe(
      "방금",
    );
  });

  it("1시간 미만은 m ago", () => {
    expect(formatRelative(new Date(now - 5 * 60_000).toISOString(), now)).toBe(
      "5m ago",
    );
  });

  it("1일 미만은 h ago", () => {
    expect(
      formatRelative(new Date(now - 3 * 60 * 60_000).toISOString(), now),
    ).toBe("3h ago");
  });

  it("하루 이상은 d ago", () => {
    expect(
      formatRelative(
        new Date(now - 2 * 24 * 60 * 60_000).toISOString(),
        now,
      ),
    ).toBe("2d ago");
  });

  it("미래 시각(시계 드리프트) 은 '방금' 으로 클램프", () => {
    expect(formatRelative(new Date(now + 5 * 60_000).toISOString(), now)).toBe(
      "방금",
    );
  });

  it("파싱 불가능한 값은 '—'", () => {
    expect(formatRelative("not-a-date", now)).toBe("—");
  });
});
