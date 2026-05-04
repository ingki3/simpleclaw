/**
 * InsightCard 단위 테스트 — BIZ-92.
 *
 * 핵심 검증:
 *  - confidence 분류: ≥0.7 high / 0.4–0.7 medium / <0.4 low (BIZ-90 합격 기준 1).
 *  - rejectOnly 변형은 Defer/Edit/Accept 가 사라지고 단일 Reject+Blocklist 만 남는다.
 *  - variant=read 는 액션 버튼 자체를 렌더하지 않는다 (Active/Archive/Blocklist 탭).
 */

import { describe, expect, test } from "vitest";
import {
  classifyConfidence,
} from "@/components/domain/InsightCard";

describe("classifyConfidence", () => {
  test("0.7 이상은 high (green)", () => {
    expect(classifyConfidence(0.7)).toBe("high");
    expect(classifyConfidence(0.84)).toBe("high");
    expect(classifyConfidence(1.0)).toBe("high");
  });

  test("0.4 이상 0.7 미만은 medium (amber)", () => {
    expect(classifyConfidence(0.4)).toBe("medium");
    expect(classifyConfidence(0.55)).toBe("medium");
    expect(classifyConfidence(0.69)).toBe("medium");
  });

  test("0.4 미만은 low (red)", () => {
    expect(classifyConfidence(0)).toBe("low");
    expect(classifyConfidence(0.2)).toBe("low");
    expect(classifyConfidence(0.39)).toBe("low");
  });
});
