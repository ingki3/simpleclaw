/**
 * SystemStatusRow 단위 테스트 — 4도메인 헬스 칩 + 라우트 점프.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { SystemStatusRow } from "@/app/(shell)/dashboard/_components/SystemStatusRow";
import type { DomainHealth } from "@/app/(shell)/dashboard/_data";

const FIXTURE: DomainHealth[] = [
  { key: "daemon", label: "daemon", tone: "green", caption: "정상" },
  { key: "llm", label: "llm", tone: "green", caption: "정상" },
  { key: "webhook", label: "webhook", tone: "amber", caption: "주의" },
  { key: "cron", label: "cron", tone: "red", caption: "실패" },
];

describe("SystemStatusRow", () => {
  it("4 도메인을 순서대로 렌더한다", () => {
    render(<SystemStatusRow domains={FIXTURE} />);
    const list = screen.getByTestId("system-status-row");
    expect(list.children).toHaveLength(4);
    for (const d of FIXTURE) {
      expect(screen.getByTestId(`system-status-${d.key}`)).toBeDefined();
    }
  });

  it("각 도메인 칩이 영역 라우트로 링크된다", () => {
    render(<SystemStatusRow domains={FIXTURE} />);
    expect(
      screen
        .getByTestId("system-status-daemon")
        .getAttribute("href"),
    ).toBe("/system");
    expect(
      screen.getByTestId("system-status-llm").getAttribute("href"),
    ).toBe("/llm-router");
    expect(
      screen
        .getByTestId("system-status-webhook")
        .getAttribute("href"),
    ).toBe("/channels");
    expect(
      screen.getByTestId("system-status-cron").getAttribute("href"),
    ).toBe("/cron");
  });

  it("data-tone 속성이 HealthDot tone 과 매칭된다", () => {
    render(<SystemStatusRow domains={FIXTURE} />);
    expect(
      screen.getByTestId("system-status-webhook").getAttribute("data-tone"),
    ).toBe("amber");
    expect(
      screen.getByTestId("system-status-cron").getAttribute("data-tone"),
    ).toBe("red");
  });

  it("caption 은 sr-only 라벨로 노출된다 (색만으로 의미 전달 금지)", () => {
    render(<SystemStatusRow domains={FIXTURE} />);
    // 캡션 텍스트가 DOM 에 존재 — 시각적으로는 sr-only 이지만 보조기기에서 읽힘.
    expect(screen.getByText(/— 주의/)).toBeDefined();
    expect(screen.getByText(/— 실패/)).toBeDefined();
  });
});
