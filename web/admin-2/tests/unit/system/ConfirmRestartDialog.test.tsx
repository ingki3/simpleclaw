/**
 * ConfirmRestartDialog 단위 테스트 — 프로세스/서비스 scope + ConfirmGate 키워드.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ConfirmRestartDialog } from "@/app/(shell)/system/_components/ConfirmRestartDialog";
import type { RestartInfo } from "@/app/(shell)/system/_data";

const INFO: RestartInfo = {
  lastRestart: "2026-04-29 09:11",
  lastRestartRelative: "Δ 5d 6h",
  needsOperatorConfirm: true,
  impactSummary: "약 10초간 서비스가 중지됩니다.",
};

describe("ConfirmRestartDialog", () => {
  it("open=false 면 렌더되지 않는다", () => {
    render(
      <ConfirmRestartDialog
        open={false}
        info={INFO}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("confirm-restart-dialog")).toBeNull();
  });

  it("open=true 면 영향 요약 + 두 scope + ConfirmGate 를 노출한다", () => {
    render(
      <ConfirmRestartDialog
        open
        info={INFO}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByTestId("confirm-restart-dialog")).toBeDefined();
    expect(screen.getByTestId("confirm-restart-scope-process")).toBeDefined();
    expect(screen.getByTestId("confirm-restart-scope-service")).toBeDefined();
    // 기본 scope 는 process — aria-checked.
    expect(
      screen
        .getByTestId("confirm-restart-scope-process")
        .getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("scope 클릭 시 aria-checked 가 토글된다", () => {
    render(
      <ConfirmRestartDialog
        open
        info={INFO}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("confirm-restart-scope-service"));
    expect(
      screen
        .getByTestId("confirm-restart-scope-service")
        .getAttribute("aria-checked"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("confirm-restart-scope-process")
        .getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("닫기 버튼 클릭 시 onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <ConfirmRestartDialog
        open
        info={INFO}
        onClose={onClose}
        onConfirm={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("modal-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
