/**
 * /memory 페이지 통합 단위 테스트 — 헤더/4-variant/모달/검색/reject→blocklist.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/memory",
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

import MemoryPage from "@/app/(shell)/memory/page";

describe("MemoryPage", () => {
  it("h1 + 핵심 섹션이 모두 렌더된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "기억" }),
    ).toBeDefined();
    expect(screen.getByTestId("section-active-projects")).toBeDefined();
    expect(screen.getByTestId("section-clusters")).toBeDefined();
    expect(screen.getByTestId("section-insights")).toBeDefined();
    expect(screen.getByTestId("section-blocklist")).toBeDefined();
    expect(screen.getByTestId("memory-search")).toBeDefined();
    expect(screen.getByTestId("memory-dry-run")).toBeDefined();
    expect(screen.getByTestId("memory-counts")).toBeDefined();
  });

  it("?insights=loading 면 InsightsList 가 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("insights=loading"),
    );
    render(<MemoryPage />);
    expect(
      screen.getByTestId("memory-insights-list").getAttribute("data-state"),
    ).toBe("loading");
  });

  it("?insights=error 면 InsightsList 가 error variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("insights=error"),
    );
    render(<MemoryPage />);
    expect(
      screen.getByTestId("memory-insights-list").getAttribute("data-state"),
    ).toBe("error");
  });

  it("?projects=loading 면 ActiveProjectsPanel 이 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("projects=loading"),
    );
    render(<MemoryPage />);
    expect(
      screen
        .getByTestId("active-projects-panel")
        .getAttribute("data-state"),
    ).toBe("loading");
  });

  it("?projects=empty 면 ActiveProjectsPanel 이 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("projects=empty"),
    );
    render(<MemoryPage />);
    expect(
      screen
        .getByTestId("active-projects-panel")
        .getAttribute("data-state"),
    ).toBe("empty");
  });

  it("Dry-run Preview 버튼 클릭 시 모달이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    expect(screen.queryByTestId("dry-run-modal")).toBeNull();
    fireEvent.click(screen.getByTestId("memory-dry-run"));
    expect(screen.getByTestId("dry-run-modal")).toBeDefined();
    expect(screen.getByTestId("dry-run-changes")).toBeDefined();
  });

  it("Dry-run 모달의 닫기 버튼 클릭 시 닫힌다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    fireEvent.click(screen.getByTestId("memory-dry-run"));
    fireEvent.click(screen.getByTestId("dry-run-cancel"));
    expect(screen.queryByTestId("dry-run-modal")).toBeNull();
  });

  it("출처 버튼 클릭 시 SourceDrawer 가 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    fireEvent.click(screen.getByTestId("memory-insight-ins-001-source"));
    const drawer = screen.getByTestId("source-drawer");
    expect(drawer).toBeDefined();
    expect(within(drawer).getByTestId("source-drawer-meta")).toBeDefined();
  });

  it("거절 버튼 클릭 → RejectConfirmModal 오픈 → 확정 → blocklist 추가", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);

    fireEvent.click(screen.getByTestId("memory-insight-ins-001-reject"));
    expect(screen.getByTestId("reject-confirm-modal")).toBeDefined();

    fireEvent.click(screen.getByTestId("reject-confirm-submit"));

    // 모달이 닫히고 blocklist 표에 morning-briefing 항목이 추가된다.
    expect(screen.queryByTestId("reject-confirm-modal")).toBeNull();
    expect(screen.getByTestId("blocklist-morning-briefing")).toBeDefined();
    // 큐에서도 제거된다 (lifecycle=archive 로 이동).
    expect(screen.queryByTestId("memory-insight-ins-001")).toBeNull();
  });

  it("채택 버튼 클릭 시 큐에서 제거된다 (lifecycle=active)", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    expect(screen.getByTestId("memory-insight-ins-002")).toBeDefined();
    fireEvent.click(screen.getByTestId("memory-insight-ins-002-accept"));
    expect(screen.queryByTestId("memory-insight-ins-002")).toBeNull();
  });

  it("검색 입력 → 토픽/본문으로 인사이트 필터링", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    fireEvent.change(screen.getByTestId("memory-search"), {
      target: { value: "stocks" },
    });
    expect(screen.getByTestId("memory-insight-ins-002")).toBeDefined();
    expect(screen.queryByTestId("memory-insight-ins-001")).toBeNull();
  });

  it("blocklist 차단 해제 클릭 시 행이 사라진다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    expect(screen.getByTestId("blocklist-joke.daily")).toBeDefined();
    fireEvent.click(screen.getByTestId("blocklist-joke.daily-unblock"));
    expect(screen.queryByTestId("blocklist-joke.daily")).toBeNull();
  });

  it("managed 토글 클릭 시 Badge 텍스트가 갱신된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    const toggle = screen.getByTestId("active-project-proj-stocks-toggle");
    expect(toggle.textContent).toContain("managed 지정");
    fireEvent.click(toggle);
    expect(
      screen
        .getByTestId("active-project-proj-stocks-toggle")
        .textContent,
    ).toContain("managed 해제");
  });

  it("페이지 헤더 카운트 — 검토/활성/차단 노출", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<MemoryPage />);
    const counts = screen.getByTestId("memory-counts");
    expect(within(counts).getByText(/검토/)).toBeDefined();
    expect(within(counts).getByText(/활성/)).toBeDefined();
    expect(within(counts).getByText(/차단/)).toBeDefined();
  });
});
