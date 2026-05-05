/**
 * RetryPolicyModal 단위 테스트 — prefill / 검증 / 저장 / 취소.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  RetryPolicyModal,
  validate,
} from "@/app/(shell)/skills-recipes/_components/RetryPolicyModal";
import { SKILL } from "./_fixture";

describe("RetryPolicyModal", () => {
  it("skill=null 이면 dialog 렌더 안 됨", () => {
    render(
      <RetryPolicyModal
        open
        skill={null}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.queryByTestId("retry-policy-modal")).toBeNull();
  });

  it("skill 이 있으면 prefill 후 헤더에 이름 노출", () => {
    render(
      <RetryPolicyModal
        open
        skill={SKILL}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.getByTestId("retry-policy-modal")).toBeDefined();
    expect(screen.getByTestId("retry-policy-modal").textContent).toContain(
      SKILL.name,
    );
    expect(
      (screen.getByTestId("retry-policy-max-attempts") as HTMLInputElement)
        .value,
    ).toBe(String(SKILL.retryPolicy.maxAttempts));
    expect(
      (screen.getByTestId("retry-policy-strategy") as HTMLSelectElement).value,
    ).toBe(SKILL.retryPolicy.backoffStrategy);
  });

  it("저장 버튼은 onSubmit(skillId, policy) + onClose 호출", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <RetryPolicyModal
        open
        skill={SKILL}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.click(screen.getByTestId("retry-policy-submit"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toBe(SKILL.id);
    expect(onSubmit.mock.calls[0]?.[1]).toMatchObject({
      maxAttempts: SKILL.retryPolicy.maxAttempts,
      backoffStrategy: SKILL.retryPolicy.backoffStrategy,
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("maxAttempts 가 0 이면 검증 실패 + onSubmit 미호출", () => {
    const onSubmit = vi.fn();
    render(
      <RetryPolicyModal
        open
        skill={SKILL}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByTestId("retry-policy-max-attempts"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByTestId("retry-policy-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("retry-policy-max-attempts-error"),
    ).toBeDefined();
  });

  it("전략을 none 으로 바꾸면 backoff 입력이 disabled", () => {
    render(
      <RetryPolicyModal
        open
        skill={SKILL}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("retry-policy-strategy"), {
      target: { value: "none" },
    });
    const backoff = screen.getByTestId(
      "retry-policy-backoff",
    ) as HTMLInputElement;
    expect(backoff.disabled).toBe(true);
  });

  it("취소 버튼은 onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <RetryPolicyModal
        open
        skill={SKILL}
        onClose={onClose}
        onSubmit={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("retry-policy-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("validate(retry-policy)", () => {
  it("정상 정책은 빈 객체 반환", () => {
    const errs = validate({
      maxAttempts: 3,
      backoffSeconds: 1,
      backoffStrategy: "exponential",
      timeoutSeconds: 30,
    });
    expect(Object.keys(errs)).toHaveLength(0);
  });

  it("maxAttempts 가 소수면 실패", () => {
    const errs = validate({
      maxAttempts: 1.5,
      backoffSeconds: 1,
      backoffStrategy: "fixed",
      timeoutSeconds: 30,
    });
    expect(errs.maxAttempts).toBeDefined();
  });

  it("backoffStrategy=none 이면 backoff 음수도 통과 (사용 안됨)", () => {
    const errs = validate({
      maxAttempts: 1,
      backoffSeconds: -10,
      backoffStrategy: "none",
      timeoutSeconds: 5,
    });
    expect(errs.backoffSeconds).toBeUndefined();
  });

  it("timeoutSeconds 가 0 이하면 실패", () => {
    const errs = validate({
      maxAttempts: 2,
      backoffSeconds: 0,
      backoffStrategy: "fixed",
      timeoutSeconds: 0,
    });
    expect(errs.timeoutSeconds).toBeDefined();
  });
});
