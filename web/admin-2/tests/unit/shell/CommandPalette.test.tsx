/**
 * CommandPalette 단위 테스트 — open/close · 키보드 nav · 영역 jump.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import { CommandPalette } from "@/app/(shell)/_components/CommandPalette";

describe("CommandPalette", () => {
  it("open=false 면 아무것도 렌더하지 않는다", () => {
    render(<CommandPalette open={false} onClose={() => {}} />);
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("open=true 면 listbox 와 11개 영역 항목이 보인다", () => {
    render(<CommandPalette open onClose={() => {}} />);
    expect(screen.getByTestId("command-palette")).toBeDefined();
    expect(
      screen.getByTestId("command-palette-list").querySelectorAll('[role="option"]').length,
    ).toBe(11);
  });

  it("입력으로 결과가 필터된다", () => {
    render(<CommandPalette open onClose={() => {}} />);
    const input = screen.getByTestId("command-palette-input");
    fireEvent.change(input, { target: { value: "secret" } });
    expect(screen.getByTestId("command-palette-item-secrets")).toBeDefined();
    expect(screen.queryByTestId("command-palette-item-cron")).toBeNull();
  });

  it("매칭 0건이면 empty 안내", () => {
    render(<CommandPalette open onClose={() => {}} />);
    fireEvent.change(screen.getByTestId("command-palette-input"), {
      target: { value: "zzz-no-match" },
    });
    expect(screen.getByTestId("command-palette-empty")).toBeDefined();
  });

  it("ESC 로 닫힌다", () => {
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} />);
    fireEvent.keyDown(screen.getByTestId("command-palette"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("↓ 키로 active 항목이 이동한다", () => {
    render(<CommandPalette open onClose={() => {}} />);
    const dialog = screen.getByTestId("command-palette");
    // 초기 active = dashboard (index 0)
    expect(
      screen.getByTestId("command-palette-item-dashboard").getAttribute("aria-selected"),
    ).toBe("true");

    fireEvent.keyDown(dialog, { key: "ArrowDown" });
    expect(
      screen
        .getByTestId("command-palette-item-llm-router")
        .getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("↑ 키는 마지막에서 wrap 된다", () => {
    render(<CommandPalette open onClose={() => {}} />);
    const dialog = screen.getByTestId("command-palette");
    fireEvent.keyDown(dialog, { key: "ArrowUp" });
    expect(
      screen.getByTestId("command-palette-item-system").getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("Enter 로 선택 시 router.push 와 onClose 가 호출된다", () => {
    pushMock.mockClear();
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} />);
    fireEvent.change(screen.getByTestId("command-palette-input"), {
      target: { value: "cron" },
    });
    fireEvent.keyDown(screen.getByTestId("command-palette"), { key: "Enter" });
    expect(pushMock).toHaveBeenCalledWith("/cron");
    expect(onClose).toHaveBeenCalled();
  });

  it("바깥 영역 클릭으로 닫히고, 내부 클릭은 닫지 않는다", () => {
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} />);
    fireEvent.click(screen.getByTestId("command-palette"));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("command-palette-input"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("항목 클릭으로 router.push 와 onClose 가 호출된다", () => {
    pushMock.mockClear();
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} />);
    fireEvent.click(screen.getByTestId("command-palette-item-secrets"));
    expect(pushMock).toHaveBeenCalledWith("/secrets");
    expect(onClose).toHaveBeenCalled();
  });
});
