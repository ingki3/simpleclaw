/**
 * 디자인 토큰 회귀 테스트 — admin.pen ThIdV/tY3NP 매핑이 깨지지 않았는지 확인.
 *
 * 본 테스트는 *값* 자체를 박제한다 — 토큰 변경 시 본 테스트도 같이 갱신.
 * 의도적으로 fragile 하게 작성해 무의식적인 색상 drift 를 차단.
 */
import { describe, expect, it } from "vitest";
import { colors, radius, spacing, typography } from "@/design/tokens";

describe("Design tokens — light palette (admin.pen ThIdV)", () => {
  it("brand-500 / primary 가 #5b6cf6 이다", () => {
    expect(colors.light.brand500).toBe("#5b6cf6");
  });

  it("danger-500 / destructive 가 #dc2626 이다", () => {
    expect(colors.light.danger500).toBe("#dc2626");
  });

  it("neutral-0 (background) 이 흰색이다", () => {
    expect(colors.light.neutral0).toBe("#ffffff");
  });
});

describe("Design tokens — dark palette (admin.pen tY3NP)", () => {
  it("neutral-0 (background) 이 #0b0f14 이다", () => {
    expect(colors.dark.neutral0).toBe("#0b0f14");
  });

  it("brand-500 가 light 보다 밝다 (7c8bff > 5b6cf6)", () => {
    expect(colors.dark.brand500).toBe("#7c8bff");
  });
});

describe("Design tokens — radius / spacing / typography", () => {
  it("radius scale 이 DESIGN.md §2.6 표대로다", () => {
    expect(radius.sm).toBe(4);
    expect(radius.m).toBe(8);
    expect(radius.l).toBe(12);
    expect(radius.pill).toBe(9999);
  });

  it("spacing 이 4px 그리드 — 허용 값만 노출", () => {
    expect(Object.values(spacing)).toEqual([
      2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64,
    ]);
  });

  it("base text size 가 14/22 (DESIGN.md §2.4)", () => {
    expect(typography.scale.base).toEqual({ size: 14, line: 22, weight: 400 });
  });
});
