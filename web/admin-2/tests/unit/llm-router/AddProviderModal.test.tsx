/**
 * AddProviderModal 단위 테스트 — 검증 / 제출 / 취소 흐름.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { AddProviderModal } from "@/app/(shell)/llm-router/_components/AddProviderModal";

describe("AddProviderModal", () => {
  it("open=false 면 dialog 가 렌더되지 않는다", () => {
    render(
      <AddProviderModal open={false} onClose={() => {}} onSubmit={() => {}} />,
    );
    expect(screen.queryByTestId("add-provider-modal")).toBeNull();
  });

  it("open=true 면 폼 필드를 모두 노출", () => {
    render(
      <AddProviderModal open onClose={() => {}} onSubmit={() => {}} />,
    );
    expect(screen.getByTestId("add-provider-modal")).toBeDefined();
    expect(screen.getByTestId("add-provider-name")).toBeDefined();
    expect(screen.getByTestId("add-provider-api-key")).toBeDefined();
    expect(screen.getByTestId("add-provider-timeout")).toBeDefined();
  });

  it("이름이 비면 검증 실패 + onSubmit 미호출", () => {
    const onSubmit = vi.fn();
    render(
      <AddProviderModal open onClose={() => {}} onSubmit={onSubmit} />,
    );
    fireEvent.click(screen.getByTestId("add-provider-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-provider-name-error")).toBeDefined();
    expect(screen.getByTestId("add-provider-api-key-error")).toBeDefined();
  });

  it("필수값 충족 시 onSubmit + onClose 호출", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <AddProviderModal open onClose={onClose} onSubmit={onSubmit} />,
    );

    fireEvent.change(screen.getByTestId("add-provider-name"), {
      target: { value: "my-openai" },
    });
    fireEvent.change(screen.getByTestId("add-provider-api-key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByTestId("add-provider-submit"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      name: "my-openai",
      apiKey: "sk-test",
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("취소 버튼은 onClose 만 호출한다", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(
      <AddProviderModal open onClose={onClose} onSubmit={onSubmit} />,
    );
    fireEvent.click(screen.getByTestId("add-provider-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("이름이 허용되지 않는 문자를 포함하면 에러", () => {
    const onSubmit = vi.fn();
    render(<AddProviderModal open onClose={() => {}} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("add-provider-name"), {
      target: { value: "bad name!" },
    });
    fireEvent.change(screen.getByTestId("add-provider-api-key"), {
      target: { value: "sk-x" },
    });
    fireEvent.click(screen.getByTestId("add-provider-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-provider-name-error")).toBeDefined();
  });
});
