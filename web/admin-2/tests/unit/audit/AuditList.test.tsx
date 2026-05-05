/**
 * AuditList 단위 테스트 — 4-variant + Undo 활성/비활성 + "더 보기".
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { AuditList } from "@/app/(shell)/audit/_components/AuditList";
import {
  ENTRIES_SAMPLE,
  ENT_APPLIED_LLM,
  ENT_FAILED,
  ENT_PENDING,
  ENT_ROLLED_BACK,
  makeBulkEntries,
} from "./_fixture";

describe("AuditList", () => {
  it("default variant — 모든 entry 가 행으로 렌더된다", () => {
    render(<AuditList state="default" entries={ENTRIES_SAMPLE} />);
    expect(screen.getByTestId("audit-rows")).toBeDefined();
    expect(
      screen.getByTestId(`audit-row-${ENT_APPLIED_LLM.id}`),
    ).toBeDefined();
    expect(screen.getByTestId(`audit-row-${ENT_FAILED.id}`)).toBeDefined();
  });

  it("loading variant — aria-busy=true + 스켈레톤", () => {
    render(<AuditList state="loading" />);
    const list = screen.getByTestId("audit-list");
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("audit-list-loading")).toBeDefined();
  });

  it("error variant — alert role + retry 콜백", () => {
    const onRetry = vi.fn();
    render(<AuditList state="error" onRetry={onRetry} />);
    const err = screen.getByTestId("audit-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("audit-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("empty variant — 안내 메시지 + reason=none", () => {
    render(<AuditList state="empty" />);
    const empty = screen.getByTestId("audit-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("none");
  });

  it("default + entries=[] + searchQuery — filtered empty 안내", () => {
    render(
      <AuditList state="default" entries={[]} searchQuery="없는키워드" />,
    );
    const empty = screen.getByTestId("audit-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("filtered");
  });

  it("applied 행의 Undo 버튼은 활성, onUndo 호출 가능", () => {
    const onUndo = vi.fn();
    render(
      <AuditList
        state="default"
        entries={ENTRIES_SAMPLE}
        onUndo={onUndo}
      />,
    );
    const btn = screen.getByTestId(
      `audit-row-${ENT_APPLIED_LLM.id}-undo`,
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onUndo.mock.calls[0][0].id).toBe(ENT_APPLIED_LLM.id);
  });

  it("failed/rolled-back/pending 행의 Undo 버튼은 비활성", () => {
    const onUndo = vi.fn();
    render(
      <AuditList
        state="default"
        entries={ENTRIES_SAMPLE}
        onUndo={onUndo}
      />,
    );
    for (const id of [ENT_FAILED.id, ENT_ROLLED_BACK.id, ENT_PENDING.id]) {
      const btn = screen.getByTestId(
        `audit-row-${id}-undo`,
      ) as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    }
    expect(onUndo).not.toHaveBeenCalled();
  });

  it("entry 가 PAGE_SIZE(10) 보다 많으면 첫 화면에 10건 + '더 보기' 노출", () => {
    const entries = makeBulkEntries(15);
    render(<AuditList state="default" entries={entries} />);
    expect(screen.getByTestId("audit-list-count").textContent).toContain(
      "10 / 15",
    );
    expect(screen.getByTestId("audit-list-more")).toBeDefined();
  });

  it("'더 보기' 클릭 시 다음 PAGE_SIZE 만큼 누적 노출", () => {
    const entries = makeBulkEntries(15);
    render(<AuditList state="default" entries={entries} />);
    fireEvent.click(screen.getByTestId("audit-list-more"));
    expect(screen.getByTestId("audit-list-count").textContent).toContain(
      "15 / 15",
    );
    expect(screen.queryByTestId("audit-list-more")).toBeNull();
  });

  it("data-state 속성이 4-variant 와 일치한다", () => {
    const { rerender } = render(<AuditList state="default" entries={[]} />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("default");
    rerender(<AuditList state="loading" />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("loading");
    rerender(<AuditList state="empty" />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("empty");
    rerender(<AuditList state="error" />);
    expect(
      screen.getByTestId("audit-list").getAttribute("data-state"),
    ).toBe("error");
  });

  it("before/after 가 모두 있는 행은 두 값과 화살표가 노출", () => {
    render(<AuditList state="default" entries={ENTRIES_SAMPLE} />);
    expect(
      screen.getByTestId(`audit-row-${ENT_APPLIED_LLM.id}-before`),
    ).toBeDefined();
    expect(
      screen.getByTestId(`audit-row-${ENT_APPLIED_LLM.id}-after`),
    ).toBeDefined();
  });
});
