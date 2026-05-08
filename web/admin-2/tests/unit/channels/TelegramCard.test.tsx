/**
 * TelegramCard 단위 테스트 — 토큰/Allowlist 표시, 콜백 invoke.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TelegramCard } from "@/app/(shell)/channels/_components/TelegramCard";
import { TELEGRAM } from "./_fixture";

function renderCard(overrides?: Partial<Parameters<typeof TelegramCard>[0]>) {
  const props = {
    channel: TELEGRAM,
    allowlistInput: TELEGRAM.allowlist.join(", "),
    onAllowlistChange: vi.fn(),
    onRotateToken: vi.fn(),
    onSendTest: vi.fn(),
    onSave: vi.fn(),
    ...overrides,
  };
  render(<TelegramCard {...props} />);
  return props;
}

describe("TelegramCard", () => {
  it("status pill 라벨과 마스킹 토큰을 노출한다", () => {
    renderCard();
    expect(screen.getByText(TELEGRAM.statusLabel)).toBeDefined();
    expect(screen.getByText(TELEGRAM.tokenMasked)).toBeDefined();
    // secret URI 도 노출 — 운영자가 어디서 토큰을 읽는지 알 수 있도록.
    expect(
      screen.getByText(`(${TELEGRAM.tokenSecretUri})`),
    ).toBeDefined();
  });

  it("'회전' 클릭 시 onRotateToken 콜백이 호출된다", () => {
    const props = renderCard();
    fireEvent.click(screen.getByTestId("telegram-bot-token-rotate"));
    expect(props.onRotateToken).toHaveBeenCalledTimes(1);
  });

  it("Allowlist 입력에 따른 chat 카운트가 trailing 에 노출된다", () => {
    renderCard({ allowlistInput: "1, 2, 3" });
    // trailing 안의 텍스트를 부분 매칭.
    expect(screen.getByText("3 chat")).toBeDefined();
  });

  it("'테스트 메시지' / '저장' 버튼이 각자 콜백을 호출한다", () => {
    const props = renderCard();
    fireEvent.click(screen.getByTestId("telegram-test"));
    fireEvent.click(screen.getByTestId("telegram-save"));
    expect(props.onSendTest).toHaveBeenCalledTimes(1);
    expect(props.onSave).toHaveBeenCalledTimes(1);
  });
});
