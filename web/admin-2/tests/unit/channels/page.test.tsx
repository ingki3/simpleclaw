/**
 * /channels 페이지 통합 단위 테스트 — 섹션 렌더 + 4-variant + 모달 트리거.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/channels",
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import ChannelsPage from "@/app/(shell)/channels/page";

describe("ChannelsPage", () => {
  it("h1 + Telegram/Webhook 섹션을 모두 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<ChannelsPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "채널" }),
    ).toBeDefined();
    expect(screen.getByTestId("telegram-card")).toBeDefined();
    expect(screen.getByTestId("webhook-list")).toBeDefined();
  });

  it("?webhooks=loading 이면 WebhookList 가 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("webhooks=loading"),
    );
    render(<ChannelsPage />);
    expect(
      screen.getByTestId("webhook-list").getAttribute("data-state"),
    ).toBe("loading");
    expect(screen.getByTestId("webhook-list-loading")).toBeDefined();
  });

  it("?webhooks=empty 이면 WebhookList 가 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("webhooks=empty"),
    );
    render(<ChannelsPage />);
    expect(
      screen.getByTestId("webhook-list").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("webhook-list-empty")).toBeDefined();
  });

  it("?webhooks=error 이면 WebhookList 가 error variant", () => {
    mockSearchParams.mockReturnValueOnce(
      new URLSearchParams("webhooks=error"),
    );
    render(<ChannelsPage />);
    expect(
      screen.getByTestId("webhook-list").getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("webhook-list-error")).toBeDefined();
  });

  it("Telegram '회전' 버튼 클릭 시 TokenRotateModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<ChannelsPage />);
    fireEvent.click(screen.getByTestId("telegram-bot-token-rotate"));
    expect(screen.getByTestId("token-rotate-modal")).toBeDefined();
  });

  it("endpoint 행 '편집' 클릭 시 WebhookEditModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<ChannelsPage />);
    fireEvent.click(screen.getByTestId("webhook-endpoint-github-edit"));
    expect(screen.getByTestId("webhook-edit-modal")).toBeDefined();
  });

  it("WebhookEditModal 의 '트래픽 시뮬레이션' 클릭 시 TrafficSimulationModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<ChannelsPage />);
    fireEvent.click(screen.getByTestId("webhook-endpoint-github-edit"));
    fireEvent.click(screen.getByTestId("webhook-edit-simulate"));
    expect(screen.getByTestId("traffic-simulation-modal")).toBeDefined();
  });

  it("Telegram 회전 모달 안에서 'ROTATE' 입력 후 카운트다운 0 시 confirm 활성화", async () => {
    vi.useFakeTimers();
    try {
      mockSearchParams.mockReturnValueOnce(new URLSearchParams());
      render(<ChannelsPage />);
      fireEvent.click(screen.getByTestId("telegram-bot-token-rotate"));
      const confirm = screen.getByTestId(
        "token-rotate-confirm",
      ) as HTMLButtonElement;
      // 키워드 입력 전: disabled.
      expect(confirm.disabled).toBe(true);
      fireEvent.change(screen.getByTestId("token-rotate-keyword"), {
        target: { value: "ROTATE" },
      });
      // 키워드 일치 직후에도 카운트다운 미경과 → 여전히 disabled.
      expect(confirm.disabled).toBe(true);
      // 1초씩 4번 act 안에서 advance — React state flush 보장.
      for (let i = 0; i < 4; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000);
        });
      }
      expect(confirm.disabled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
