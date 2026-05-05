/**
 * RejectConfirmModal 단위 테스트 — duration select / reason / onConfirm payload.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  RejectConfirmModal,
  normalizeTopic,
} from "@/app/(shell)/memory/_components/RejectConfirmModal";
import { INSIGHT_REVIEW } from "./_fixture";

describe("RejectConfirmModal", () => {
  it("target=null 일 때는 모달이 열리지 않는다", () => {
    render(
      <RejectConfirmModal
        target={null}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByTestId("reject-confirm-modal")).toBeNull();
  });

  it("target 이 있으면 모달 + 대상 카드가 렌더된다", () => {
    render(
      <RejectConfirmModal
        target={INSIGHT_REVIEW}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByTestId("reject-confirm-modal")).toBeDefined();
    const target = screen.getByTestId("reject-confirm-target");
    expect(target.textContent).toContain(INSIGHT_REVIEW.topic);
  });

  it("취소 버튼 클릭 시 onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <RejectConfirmModal
        target={INSIGHT_REVIEW}
        onClose={onClose}
        onConfirm={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("reject-confirm-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("기본값으로 onConfirm — duration=30d / reason='' / topicKey 정규형", () => {
    const onConfirm = vi.fn();
    render(
      <RejectConfirmModal
        target={INSIGHT_REVIEW}
        onClose={() => {}}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.click(screen.getByTestId("reject-confirm-submit"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith({
      insightId: INSIGHT_REVIEW.id,
      topicKey: normalizeTopic(INSIGHT_REVIEW.topic),
      reason: "",
      duration: "30d",
    });
  });

  it("duration 변경 + reason 입력 → onConfirm payload 반영", () => {
    const onConfirm = vi.fn();
    render(
      <RejectConfirmModal
        target={INSIGHT_REVIEW}
        onClose={() => {}}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.change(screen.getByTestId("reject-confirm-duration"), {
      target: { value: "forever" },
    });
    fireEvent.change(screen.getByTestId("reject-confirm-reason"), {
      target: { value: "  반복 농담  " },
    });
    fireEvent.click(screen.getByTestId("reject-confirm-submit"));
    expect(onConfirm).toHaveBeenCalledWith({
      insightId: INSIGHT_REVIEW.id,
      topicKey: normalizeTopic(INSIGHT_REVIEW.topic),
      reason: "반복 농담",
      duration: "forever",
    });
  });
});

describe("normalizeTopic", () => {
  it("trim + lowercase", () => {
    expect(normalizeTopic("  Joke.Daily  ")).toBe("joke.daily");
  });
});
