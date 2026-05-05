/**
 * ActiveProjectsPanel 단위 테스트 — 4-variant (default/empty/loading/error).
 *
 * DESIGN.md §1 Principle 3 — 모든 영역에 4-variant 가 시각적으로 구별 가능해야 한다.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

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

import { ActiveProjectsPanel } from "@/app/(shell)/dashboard/_components/ActiveProjectsPanel";
import type { ActiveProject } from "@/app/(shell)/dashboard/_data";

const PROJECTS: ActiveProject[] = [
  {
    id: "BIZ-1",
    identifier: "BIZ-1",
    title: "Project 1",
    statusLabel: "in_progress",
    statusTone: "info",
    owner: "Dev",
    updatedAt: "방금",
    excerpt: "summary 1",
  },
  {
    id: "BIZ-2",
    identifier: "BIZ-2",
    title: "Project 2",
    statusLabel: "done",
    statusTone: "success",
    owner: "Biz",
    updatedAt: "어제",
    excerpt: "summary 2",
  },
];

describe("ActiveProjectsPanel", () => {
  it("default state — 항목들을 렌더하고 data-state='default' 를 노출", () => {
    render(<ActiveProjectsPanel state="default" projects={PROJECTS} />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("default");
    expect(screen.getByTestId("active-projects-list")).toBeDefined();
    expect(screen.getByTestId("active-project-BIZ-1")).toBeDefined();
    expect(screen.getByTestId("active-project-BIZ-2")).toBeDefined();
    expect(screen.getByText("Project 1")).toBeDefined();
  });

  it("loading state — 스켈레톤 + aria-busy", () => {
    render(<ActiveProjectsPanel state="loading" />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("loading");
    expect(panel.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("active-projects-loading")).toBeDefined();
  });

  it("empty state — EmptyState + 기억 영역 링크 노출", () => {
    render(<ActiveProjectsPanel state="empty" />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("empty");
    expect(screen.getByTestId("active-projects-empty")).toBeDefined();
    expect(screen.getByText(/비어 있습니다/)).toBeDefined();
  });

  it("error state — alert role + 메시지 노출, 재시도 버튼 동작", () => {
    const onRetry = vi.fn();
    render(
      <ActiveProjectsPanel
        state="error"
        errorMessage="네트워크 오류"
        onRetry={onRetry}
      />,
    );
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("error");

    const errorBox = screen.getByTestId("active-projects-error");
    expect(errorBox.getAttribute("role")).toBe("alert");
    expect(errorBox.textContent).toContain("네트워크 오류");

    fireEvent.click(screen.getByTestId("active-projects-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("default state 에서 projects 가 0개면 empty 폴백", () => {
    render(<ActiveProjectsPanel state="default" projects={[]} />);
    expect(screen.getByTestId("active-projects-empty")).toBeDefined();
  });
});
