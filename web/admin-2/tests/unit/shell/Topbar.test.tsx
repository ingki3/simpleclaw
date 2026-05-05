/**
 * Topbar 단위 테스트 — breadcrumb · ⌘K 트리거 · theme toggle 영속.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/persona"),
}));

import { Topbar } from "@/app/(shell)/_components/Topbar";
import { ThemeProvider } from "@/design/ThemeProvider";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

function renderTopbar(overrides: { onOpenPalette?: () => void } = {}) {
  const onOpenPalette = overrides.onOpenPalette ?? vi.fn();
  render(
    <ThemeProvider>
      <Topbar onOpenPalette={onOpenPalette} />
    </ThemeProvider>,
  );
  return { onOpenPalette };
}

describe("Topbar", () => {
  it("현재 영역의 label 을 breadcrumb 에 노출한다", () => {
    renderTopbar();
    const crumb = screen.getByTestId("topbar-breadcrumb");
    expect(crumb.textContent).toContain("페르소나");
  });

  it("⌘K 트리거 클릭 시 onOpenPalette 가 호출된다", () => {
    const { onOpenPalette } = renderTopbar();
    fireEvent.click(screen.getByTestId("topbar-palette-trigger"));
    expect(onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it("theme toggle 클릭이 ThemeProvider 와 localStorage 에 반영된다", () => {
    renderTopbar();
    fireEvent.click(screen.getByTestId("theme-toggle-dark"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("simpleclaw.admin2.theme")).toBe("dark");

    fireEvent.click(screen.getByTestId("theme-toggle-light"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem("simpleclaw.admin2.theme")).toBe("light");

    fireEvent.click(screen.getByTestId("theme-toggle-system"));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem("simpleclaw.admin2.theme")).toBe(
      "system",
    );
  });

  it("aria-checked 가 현재 mode 와 동기화된다", () => {
    renderTopbar();
    fireEvent.click(screen.getByTestId("theme-toggle-dark"));
    expect(
      screen.getByTestId("theme-toggle-dark").getAttribute("aria-checked"),
    ).toBe("true");
    expect(
      screen.getByTestId("theme-toggle-light").getAttribute("aria-checked"),
    ).toBe("false");
  });
});
