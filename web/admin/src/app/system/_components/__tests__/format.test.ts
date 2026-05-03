/**
 * format 유틸 단위 테스트 — System 화면의 표현 헬퍼.
 */

import { describe, expect, test } from "vitest";
import { formatBytes, formatUptime, percent } from "../format";

describe("formatBytes", () => {
  test("0 또는 음수는 보수적으로 0 B 또는 — 처리", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
  });

  test("KB/MB/GB 단위로 자동 환산", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1024 * 1024)).toBe("1 MB");
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
  });
});

describe("formatUptime", () => {
  test("60초 미만은 초만 표기", () => {
    expect(formatUptime(0)).toBe("0s");
    expect(formatUptime(45)).toBe("45s");
  });

  test("분/시/일 단위로 자동 그룹핑", () => {
    expect(formatUptime(60)).toBe("1m");
    expect(formatUptime(125)).toBe("2m 5s");
    expect(formatUptime(3600)).toBe("1h");
    expect(formatUptime(3660)).toBe("1h 1m");
    expect(formatUptime(86400)).toBe("1d");
    expect(formatUptime(86400 + 3600 * 5)).toBe("1d 5h");
  });

  test("부적절한 입력은 — 으로", () => {
    expect(formatUptime(undefined)).toBe("—");
    expect(formatUptime(null)).toBe("—");
    expect(formatUptime(-5)).toBe("—");
  });
});

describe("percent", () => {
  test("0~1 비율을 정수 % 문자열로", () => {
    expect(percent(0)).toBe("0%");
    expect(percent(0.5)).toBe("50%");
    expect(percent(0.999)).toBe("100%");
  });
});
