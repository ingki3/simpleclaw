/**
 * Sidebar 단위 테스트 — 11개 영역 nav · active 표시 · collapse · search.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/cron"),
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

import { Sidebar } from "@/app/(shell)/_components/Sidebar";
import { AREAS } from "@/app/areas";

describe("Sidebar", () => {
  it("11개 영역 링크를 렌더한다", () => {
    render(<Sidebar />);
    for (const area of AREAS) {
      const link = screen.getByTestId(`sidebar-link-${area.path.slice(1)}`);
      expect(link.getAttribute("href")).toBe(area.path);
    }
  });

  it("현재 pathname 의 영역이 active 표시된다", () => {
    render(<Sidebar />);
    const activeLink = screen.getByTestId("sidebar-link-cron");
    expect(activeLink.getAttribute("aria-current")).toBe("page");
    expect(activeLink.getAttribute("data-active")).toBe("true");

    const inactiveLink = screen.getByTestId("sidebar-link-dashboard");
    expect(inactiveLink.getAttribute("aria-current")).toBeNull();
  });

  it("collapse 토글 시 data-collapsed 가 적용된다", () => {
    render(<Sidebar />);
    const aside = screen.getByTestId("sidebar");
    expect(aside.getAttribute("data-collapsed")).toBeNull();

    fireEvent.click(screen.getByTestId("sidebar-collapse"));
    expect(aside.getAttribute("data-collapsed")).toBe("true");
  });

  it("search 입력으로 항목이 필터된다", () => {
    render(<Sidebar />);
    const search = screen.getByTestId("sidebar-search") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "크론" } });

    expect(screen.getByTestId("sidebar-link-cron")).toBeDefined();
    expect(screen.queryByTestId("sidebar-link-dashboard")).toBeNull();
  });

  it("매칭 0건이면 안내 문구를 노출한다", () => {
    render(<Sidebar />);
    const search = screen.getByTestId("sidebar-search") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "no-match-xyz" } });
    expect(screen.getByText(/일치하는 영역이 없습니다/)).toBeDefined();
  });

  it("daemon 상태/버전을 footer 에 노출한다", () => {
    render(<Sidebar daemonStatus="online" daemonVersion="v0.6.1" />);
    const footer = screen.getByTestId("sidebar-footer");
    expect(footer.textContent).toContain("정상");
    expect(footer.textContent).toContain("v0.6.1");
  });
});
