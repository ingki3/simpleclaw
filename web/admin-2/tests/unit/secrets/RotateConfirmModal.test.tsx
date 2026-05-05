/**
 * RotateConfirmModal 단위 테스트 — ConfirmGate 키워드/카운트다운 + 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { RotateConfirmModal } from "@/app/(shell)/secrets/_components/RotateConfirmModal";
import { SECRET_HOT } from "./_fixture";

describe("RotateConfirmModal", () => {
  it("target=null 이면 모달이 렌더되지 않는다", () => {
    render(
      <RotateConfirmModal
        target={null}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("rotate-confirm-modal")).toBeNull();
  });

  it("target 이 있으면 alertdialog role + 키 이름 + maskedPreview + ConfirmGate 노출", () => {
    render(
      <RotateConfirmModal
        target={SECRET_HOT}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    const modal = screen.getByTestId("rotate-confirm-modal");
    expect(modal.getAttribute("role")).toBe("alertdialog");
    expect(screen.getByTestId("rotate-confirm-target").textContent).toContain(
      SECRET_HOT.keyName,
    );
    expect(screen.getByTestId("rotate-confirm-target").textContent).toContain(
      SECRET_HOT.maskedPreview,
    );
    expect(screen.getByTestId("rotate-confirm-gate")).toBeDefined();
  });

  it("취소 버튼 → onClose", () => {
    const onClose = vi.fn();
    render(
      <RotateConfirmModal
        target={SECRET_HOT}
        onClose={onClose}
        onConfirm={() => {}}
        countdownSeconds={1}
      />,
    );
    fireEvent.click(screen.getByText("취소"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("키워드 미입력 상태에서 회전 실행 버튼은 disabled — onConfirm 미호출", () => {
    const onConfirm = vi.fn();
    render(
      <RotateConfirmModal
        target={SECRET_HOT}
        onClose={() => {}}
        onConfirm={onConfirm}
        countdownSeconds={1}
      />,
    );
    const button = screen.getByRole("button", { name: "회전 실행" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("정확한 키워드 + 카운트다운 종료 후 onConfirm(secret) 호출", async () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    try {
      render(
        <RotateConfirmModal
          target={SECRET_HOT}
          onClose={() => {}}
          onConfirm={onConfirm}
          countdownSeconds={1}
        />,
      );
      const input = screen.getByLabelText("confirm keyword") as HTMLInputElement;
      fireEvent.change(input, { target: { value: SECRET_HOT.keyName } });
      // 카운트다운 1초 진행 후 활성화.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });
      const button = screen.getByRole("button", {
        name: "회전 실행",
      }) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      fireEvent.click(button);
      expect(onConfirm).toHaveBeenCalledTimes(1);
      expect(onConfirm).toHaveBeenCalledWith(SECRET_HOT);
    } finally {
      vi.useRealTimers();
    }
  });

  it("틀린 키워드면 카운트다운이 시작되지 않는다", async () => {
    vi.useFakeTimers();
    try {
      render(
        <RotateConfirmModal
          target={SECRET_HOT}
          onClose={() => {}}
          onConfirm={() => {}}
          countdownSeconds={1}
        />,
      );
      fireEvent.change(screen.getByLabelText("confirm keyword"), {
        target: { value: "rotate" },
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      const button = screen.getByRole("button", {
        name: "회전 실행",
      }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
