/**
 * ActiveProjectsPanel 단위 테스트 — 4-variant + managed 토글 + retry 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  ActiveProjectsPanel,
  formatRelative,
} from "@/app/(shell)/memory/_components/ActiveProjectsPanel";
import { PROJECT_LIST, PROJECT_MANAGED } from "./_fixture";

describe("ActiveProjectsPanel", () => {
  it("default variant — 프로젝트 행 + 카운트 노출", () => {
    render(<ActiveProjectsPanel state="default" projects={PROJECT_LIST} />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("data-state")).toBe("default");
    expect(screen.getByTestId("active-projects-list")).toBeDefined();
    expect(screen.getByTestId(`active-project-${PROJECT_MANAGED.id}`)).toBeDefined();
    expect(screen.getByTestId("active-projects-counts")).toBeDefined();
  });

  it("loading variant — aria-busy=true + 스켈레톤", () => {
    render(<ActiveProjectsPanel state="loading" />);
    const panel = screen.getByTestId("active-projects-panel");
    expect(panel.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("active-projects-loading")).toBeDefined();
  });

  it("empty variant — EmptyState 노출", () => {
    render(<ActiveProjectsPanel state="empty" />);
    expect(
      screen.getByTestId("active-projects-panel").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("active-projects-empty")).toBeDefined();
  });

  it("error variant — alert role + retry 콜백", () => {
    const onRetry = vi.fn();
    render(<ActiveProjectsPanel state="error" onRetry={onRetry} />);
    const err = screen.getByTestId("active-projects-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("active-projects-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("default variant + projects=[] — empty fallback", () => {
    render(<ActiveProjectsPanel state="default" projects={[]} />);
    expect(screen.getByTestId("active-projects-empty")).toBeDefined();
  });

  it("managed 토글 클릭 시 onToggleManaged(id, next) 호출", () => {
    const onToggle = vi.fn();
    render(
      <ActiveProjectsPanel
        state="default"
        projects={PROJECT_LIST}
        onToggleManaged={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByTestId(`active-project-${PROJECT_MANAGED.id}-toggle`),
    );
    expect(onToggle).toHaveBeenCalledWith(
      PROJECT_MANAGED.id,
      !PROJECT_MANAGED.managed,
    );
  });
});

describe("formatRelative", () => {
  const NOW = new Date("2026-05-05T10:00:00.000Z").getTime();

  it("1분 미만 → '방금'", () => {
    expect(formatRelative("2026-05-05T09:59:30.000Z", NOW)).toBe("방금");
  });

  it("Nm ago", () => {
    expect(formatRelative("2026-05-05T09:30:00.000Z", NOW)).toBe("30m ago");
  });

  it("Nh ago", () => {
    expect(formatRelative("2026-05-05T07:00:00.000Z", NOW)).toBe("3h ago");
  });

  it("Nd ago", () => {
    expect(formatRelative("2026-05-03T10:00:00.000Z", NOW)).toBe("2d ago");
  });

  it("음수(미래) — '방금'", () => {
    expect(formatRelative("2026-05-05T10:30:00.000Z", NOW)).toBe("방금");
  });

  it("invalid → '—'", () => {
    expect(formatRelative("not-a-date", NOW)).toBe("—");
  });
});
