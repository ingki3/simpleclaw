/**
 * Dashboard 페이지 통합 단위 테스트 — 5개 섹션이 모두 렌더되는지 + 4-variant 쿼리.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/dashboard",
}));

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

import DashboardPage from "@/app/(shell)/dashboard/page";

describe("DashboardPage", () => {
  it("h1 '대시보드' 와 5개 섹션을 모두 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<DashboardPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "대시보드" }),
    ).toBeDefined();
    expect(screen.getByTestId("system-status-row")).toBeDefined();
    expect(screen.getByTestId("dashboard-metrics")).toBeDefined();
    expect(screen.getByTestId("active-projects-panel")).toBeDefined();
    expect(screen.getByTestId("recent-activity")).toBeDefined();
    expect(screen.getByTestId("recent-alerts")).toBeDefined();
  });

  it("기본 상태에서 ActiveProjectsPanel 은 default state 를 갖는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<DashboardPage />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("default");
  });

  it("?activeProjects=loading 이면 loading variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("activeProjects=loading"),
    );
    render(<DashboardPage />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("loading");
    expect(screen.getByTestId("active-projects-loading")).toBeDefined();
  });

  it("?activeProjects=empty 이면 empty variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("activeProjects=empty"),
    );
    render(<DashboardPage />);
    expect(
      screen
        .getByTestId("active-projects-panel")
        .getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("active-projects-empty")).toBeDefined();
  });

  it("?activeProjects=error 이면 error variant 로 전환된다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("activeProjects=error"),
    );
    render(<DashboardPage />);
    expect(
      screen
        .getByTestId("active-projects-panel")
        .getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("active-projects-error")).toBeDefined();
  });

  it("알 수 없는 ?activeProjects 값은 default 로 폴백한다", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("activeProjects=garbage"),
    );
    render(<DashboardPage />);
    expect(
      screen
        .getByTestId("active-projects-panel")
        .getAttribute("data-state"),
    ).toBe("default");
  });
});
