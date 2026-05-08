/**
 * UndoConfirmModal 단위 테스트 — open/close, 요약 카드, confirm/cancel 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { UndoConfirmModal } from "@/app/(shell)/audit/_components/UndoConfirmModal";
import { ENT_APPLIED_LLM, ENT_APPLIED_SECRET } from "./_fixture";

describe("UndoConfirmModal", () => {
  it("entry=null 이면 렌더되지 않는다", () => {
    render(
      <UndoConfirmModal
        open={true}
        entry={null}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("undo-confirm-modal")).toBeNull();
  });

  it("entry 가 있으면 dialog + 요약 카드 + before/after 가 노출", () => {
    render(
      <UndoConfirmModal
        open={true}
        entry={ENT_APPLIED_LLM}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByTestId("undo-confirm-modal")).toBeDefined();
    expect(
      screen.getByRole("heading", { level: 2, name: "변경 되돌리기" }),
    ).toBeDefined();
    expect(screen.getByTestId("undo-confirm-target").textContent).toBe(
      ENT_APPLIED_LLM.target,
    );
    expect(screen.getByTestId("undo-confirm-field").textContent).toBe(
      ENT_APPLIED_LLM.field!,
    );
    expect(screen.getByTestId("undo-confirm-before").textContent).toBe(
      ENT_APPLIED_LLM.before!,
    );
    expect(screen.getByTestId("undo-confirm-after").textContent).toBe(
      ENT_APPLIED_LLM.after!,
    );
  });

  it("before 가 없는 entry — after 만 노출하고 before 자리는 dash", () => {
    render(
      <UndoConfirmModal
        open={true}
        entry={ENT_APPLIED_SECRET}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("undo-confirm-before")).toBeNull();
    expect(screen.getByTestId("undo-confirm-after").textContent).toBe(
      ENT_APPLIED_SECRET.after!,
    );
  });

  it("취소 버튼 → onClose 호출, onConfirm 미호출", () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <UndoConfirmModal
        open={true}
        entry={ENT_APPLIED_LLM}
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByTestId("undo-confirm-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("되돌리기 버튼 → onConfirm(entry) 호출", () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <UndoConfirmModal
        open={true}
        entry={ENT_APPLIED_LLM}
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByTestId("undo-confirm-submit"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0].id).toBe(ENT_APPLIED_LLM.id);
  });

  it("ESC 키 → onClose 호출 (Modal 공통 동작)", () => {
    const onClose = vi.fn();
    render(
      <UndoConfirmModal
        open={true}
        entry={ENT_APPLIED_LLM}
        onClose={onClose}
        onConfirm={() => {}}
      />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("open=false 이면 dialog 가 렌더되지 않는다", () => {
    render(
      <UndoConfirmModal
        open={false}
        entry={ENT_APPLIED_LLM}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("undo-confirm-modal")).toBeNull();
  });
});
