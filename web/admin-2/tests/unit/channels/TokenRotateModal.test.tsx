/**
 * TokenRotateModal 단위 테스트 — 키워드 + 카운트다운 ConfirmGate.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { TokenRotateModal } from "@/app/(shell)/channels/_components/TokenRotateModal";

afterEach(() => {
  vi.useRealTimers();
});

describe("TokenRotateModal", () => {
  it("open=false 면 dialog 가 렌더되지 않는다", () => {
    render(
      <TokenRotateModal
        open={false}
        targetLabel="Telegram Bot"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("token-rotate-modal")).toBeNull();
  });

  it("open=true 면 헤더에 targetLabel 과 경고 박스가 보인다", () => {
    render(
      <TokenRotateModal
        open
        targetLabel="Telegram Bot"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    const modal = screen.getByTestId("token-rotate-modal");
    expect(modal.textContent).toContain("Telegram Bot");
    expect(screen.getByTestId("token-rotate-warning").getAttribute("role")).toBe(
      "alert",
    );
  });

  it("키워드 미입력 + 카운트다운 미경과 시 confirm 은 disabled", () => {
    render(
      <TokenRotateModal
        open
        targetLabel="Telegram Bot"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    const confirm = screen.getByTestId(
      "token-rotate-confirm",
    ) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
  });

  it("키워드 일치 + 카운트다운 0 도달 시 confirm 활성화 + onConfirm/onClose 호출", async () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <TokenRotateModal
        open
        targetLabel="Telegram Bot"
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );
    const confirm = screen.getByTestId(
      "token-rotate-confirm",
    ) as HTMLButtonElement;
    fireEvent.change(screen.getByTestId("token-rotate-keyword"), {
      target: { value: "ROTATE" },
    });
    expect(confirm.disabled).toBe(true);
    // 중첩 setTimeout 사이클 — React state flush 를 위해 act() 안에서 advance.
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("카운트다운 중에 키워드를 잘못 고치면 카운트다운이 리셋된다", async () => {
    vi.useFakeTimers();
    render(
      <TokenRotateModal
        open
        targetLabel="Telegram Bot"
        countdownSeconds={3}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("token-rotate-keyword"), {
      target: { value: "ROTATE" },
    });
    await vi.advanceTimersByTimeAsync(2000);
    // 잘못 고침 → 카운트다운이 다시 3초로 리셋되어야 함.
    fireEvent.change(screen.getByTestId("token-rotate-keyword"), {
      target: { value: "ROTATX" },
    });
    expect(screen.getByTestId("token-rotate-remaining").textContent).toBe(
      "3초",
    );
  });

  it("취소 버튼은 onClose 만 호출한다", () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <TokenRotateModal
        open
        targetLabel="Telegram Bot"
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByTestId("token-rotate-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
