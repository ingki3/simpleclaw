/**
 * memory-server 단위 테스트 — 파서·교체·삭제·드리밍 시뮬레이션.
 *
 * 디스크 IO와 spawnSync는 호출하지 않는다 — 본 테스트는 순수 함수 동작만 검증한다.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ENTRY_TYPES,
  getDreamingState,
  parseMemoryIndex,
  removeEntry,
  replaceEntry,
  triggerDreaming,
} from "../memory-server";

describe("parseMemoryIndex", () => {
  it("맨 앞 자유 텍스트 bullet도 root 섹션으로 잡는다", () => {
    const text = "- root1\n- root2\n";
    const entries = parseMemoryIndex(text);
    expect(entries).toHaveLength(2);
    expect(entries[0].id).toBe("0:0");
    expect(entries[0].section).toBe("(root)");
    expect(entries[1].id).toBe("0:1");
  });

  it("## 헤더가 등장하면 섹션 카운터가 1 증가한다", () => {
    const text = "# Memory\n\n## 2026-04-28\n- A\n- B\n\n## 2026-04-29\n- C\n";
    const entries = parseMemoryIndex(text);
    expect(entries.map((e) => e.id)).toEqual(["2:0", "2:1", "3:0"]);
    expect(entries[0].section).toBe("2026-04-28");
    expect(entries[2].section).toBe("2026-04-29");
  });

  it("[type] prefix를 분류로 추출하고 본문에서 제거한다", () => {
    const text = "## s\n- [user] 사용자는 한국어 존댓말을 선호한다\n- [Feedback] 테스트 mock 금지\n- 그냥 항목\n";
    const entries = parseMemoryIndex(text);
    expect(entries[0].type).toBe("user");
    expect(entries[0].text).toBe("사용자는 한국어 존댓말을 선호한다");
    expect(entries[1].type).toBe("feedback");
    expect(entries[2].type).toBeNull();
    expect(entries[2].text).toBe("그냥 항목");
  });

  it("4가지 type 토큰을 모두 인식한다", () => {
    expect(ENTRY_TYPES).toEqual(["user", "feedback", "project", "reference"]);
    const text = ENTRY_TYPES.map((t) => `- [${t}] x`).join("\n");
    const entries = parseMemoryIndex(text);
    expect(entries.map((e) => e.type)).toEqual(ENTRY_TYPES);
  });

  it("빈 본문은 빈 배열을 반환한다", () => {
    expect(parseMemoryIndex("")).toEqual([]);
    expect(parseMemoryIndex("\n\n")).toEqual([]);
  });
});

describe("replaceEntry", () => {
  const sample = "# Memory\n\n## 2026-04-28\n- A\n- B\n\n## 2026-04-29\n- C\n";

  it("매칭 라인의 본문만 교체하고 마커·들여쓰기는 보존한다", () => {
    const next = replaceEntry(sample, "2:1", "B*");
    expect(next).toContain("- A\n- B*\n");
    // 다른 섹션 항목은 변경 없음
    expect(next).toContain("- C");
  });

  it("매칭 안 되는 id는 null 반환", () => {
    expect(replaceEntry(sample, "9:9", "x")).toBeNull();
    expect(replaceEntry(sample, "garbage", "x")).toBeNull();
  });
});

describe("removeEntry", () => {
  const sample = "## s\n- A\n- B\n- C\n";

  it("매칭 라인을 삭제한다", () => {
    const next = removeEntry(sample, "1:1");
    expect(next).not.toBeNull();
    // - B가 사라졌는지 확인
    const entries = parseMemoryIndex(next!);
    expect(entries.map((e) => e.text)).toEqual(["A", "C"]);
  });

  it("매칭 없으면 null", () => {
    expect(removeEntry(sample, "9:9")).toBeNull();
  });
});

describe("triggerDreaming", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    // 모듈 상태 리셋을 위해 충분히 진행
  });

  it("실행 중에 다시 트리거하면 busy", () => {
    const first = triggerDreaming();
    expect(first.ok).toBe(true);
    const second = triggerDreaming();
    expect(second.ok).toBe(false);
    expect(second.reason).toBe("busy");
    expect(getDreamingState().running).toBe(true);
    // 끝까지 실행
    vi.runAllTimers();
    expect(getDreamingState().running).toBe(false);
    expect(getDreamingState().lastOutcome).toBe("success");
  });
});
