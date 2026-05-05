/**
 * EditProviderModal 단위 테스트 — prefill / 저장 / 회전 / 삭제.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { EditProviderModal } from "@/app/(shell)/llm-router/_components/EditProviderModal";
import type { RouterProvider } from "@/app/(shell)/llm-router/_data";

const PROVIDER: RouterProvider = {
  id: "claude",
  name: "claude",
  apiType: "anthropic",
  model: "claude-opus-4-6",
  baseUrl: "api.anthropic.com/v1",
  apiKeyMasked: "sk-ant-••••3a72",
  keyringName: "claude_api_key",
  isDefault: true,
  inFallbackChain: true,
  fallbackPriority: 0,
  health: {
    tone: "success",
    label: "정상",
    avgLatencyMs: 350,
    tokens24h: "44.0k",
  },
};

describe("EditProviderModal", () => {
  it("provider=null 이면 dialog 가 렌더되지 않는다", () => {
    render(
      <EditProviderModal
        open={true}
        provider={null}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.queryByTestId("edit-provider-modal")).toBeNull();
  });

  it("provider 가 있으면 prefill 후 헤더에 이름과 keyring 노출", () => {
    render(
      <EditProviderModal
        open
        provider={PROVIDER}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.getByTestId("edit-provider-modal")).toBeDefined();
    expect(
      screen.getByTestId("edit-provider-keyring").textContent,
    ).toContain("claude_api_key");
    expect(
      (screen.getByTestId("edit-provider-model") as HTMLInputElement).value,
    ).toBe("claude-opus-4-6");
  });

  it("저장 버튼은 onSubmit + onClose 호출", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <EditProviderModal
        open
        provider={PROVIDER}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.click(screen.getByTestId("edit-provider-submit"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("회전 버튼은 onRotateSecret(providerId) 호출", () => {
    const onRotate = vi.fn();
    render(
      <EditProviderModal
        open
        provider={PROVIDER}
        onClose={() => {}}
        onSubmit={() => {}}
        onRotateSecret={onRotate}
      />,
    );
    fireEvent.click(screen.getByTestId("edit-provider-rotate"));
    expect(onRotate).toHaveBeenCalledWith("claude");
  });

  it("삭제 버튼은 onDelete + onClose 호출", () => {
    const onDelete = vi.fn();
    const onClose = vi.fn();
    render(
      <EditProviderModal
        open
        provider={PROVIDER}
        onClose={onClose}
        onSubmit={() => {}}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTestId("edit-provider-delete"));
    expect(onDelete).toHaveBeenCalledWith("claude");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("timeoutMs 가 비양수면 검증 실패", () => {
    const onSubmit = vi.fn();
    render(
      <EditProviderModal
        open
        provider={PROVIDER}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByTestId("edit-provider-timeout"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByTestId("edit-provider-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("edit-provider-timeout-error")).toBeDefined();
  });
});
