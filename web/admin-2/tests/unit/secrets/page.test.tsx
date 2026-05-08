/**
 * /secrets 페이지 통합 단위 테스트 — 헤더 / 4-variant / Add / Rotate / 검색 / 시크릿 누출 금지.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/secrets",
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

import SecretsPage from "@/app/(shell)/secrets/page";

describe("SecretsPage", () => {
  it("h1 + 검색 + 추가 버튼 + 카운트가 모두 렌더된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SecretsPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "시크릿" }),
    ).toBeDefined();
    expect(screen.getByTestId("secrets-search")).toBeDefined();
    expect(screen.getByTestId("secrets-add")).toBeDefined();
    expect(screen.getByTestId("secrets-counts")).toBeDefined();
    expect(screen.getByTestId("secrets-list")).toBeDefined();
  });

  it("?secrets=loading → SecretsList 가 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("secrets=loading"));
    render(<SecretsPage />);
    expect(screen.getByTestId("secrets-list").getAttribute("data-state")).toBe(
      "loading",
    );
  });

  it("?secrets=error → SecretsList 가 error variant + 재시도 버튼", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("secrets=error"));
    render(<SecretsPage />);
    const list = screen.getByTestId("secrets-list");
    expect(list.getAttribute("data-state")).toBe("error");
    expect(screen.getByTestId("secrets-list-retry")).toBeDefined();
  });

  it("?secrets=empty → SecretsList 가 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("secrets=empty"));
    render(<SecretsPage />);
    expect(screen.getByTestId("secrets-list-empty")).toBeDefined();
  });

  it("검색 입력 시 결과가 즉시 필터링된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SecretsPage />);
    const search = screen.getByTestId("secrets-search") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "anthropic" } });
    expect(
      screen.getByTestId("secret-row-keyring:llm.anthropic_api_key"),
    ).toBeDefined();
    expect(
      screen.queryByTestId("secret-row-keyring:llm.openai_api_key"),
    ).toBeNull();
  });

  it("'＋ 시크릿 추가' → AddSecretModal 오픈", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SecretsPage />);
    expect(screen.queryByTestId("add-secret-modal")).toBeNull();
    fireEvent.click(screen.getByTestId("secrets-add"));
    expect(screen.getByTestId("add-secret-modal")).toBeDefined();
  });

  it("Add → 정상 입력 시 fixture 에 마스킹된 새 행이 추가되고 평문은 어디에도 남지 않는다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    const consoleInfoSpy = vi
      .spyOn(console, "info")
      .mockImplementation(() => {});
    try {
      const { container } = render(<SecretsPage />);
      fireEvent.click(screen.getByTestId("secrets-add"));
      fireEvent.change(screen.getByTestId("add-secret-key-name"), {
        target: { value: "service.test_token" },
      });
      fireEvent.change(screen.getByTestId("add-secret-value"), {
        target: { value: "supersecret-PLAINTEXT-1234" },
      });
      fireEvent.click(screen.getByTestId("add-secret-submit"));

      // 모달 닫힘 + 새 행 추가.
      expect(screen.queryByTestId("add-secret-modal")).toBeNull();
      const newRow = screen.getByTestId("secret-row-keyring:service.test_token");
      expect(newRow.textContent).toContain("••••1234");
      // 평문은 DOM 에 등장 X.
      expect(container.innerHTML).not.toContain("supersecret-PLAINTEXT");
      expect(container.innerHTML).not.toContain("PLAINTEXT-1234");

      // 콘솔 박제는 키 이름·길이만 — 평문은 절대 흘리지 않음.
      const allArgs = consoleInfoSpy.mock.calls.flat().map(String).join(" ");
      expect(allArgs).toContain("service.test_token");
      expect(allArgs).toContain("len=");
      expect(allArgs).not.toContain("supersecret");
      expect(allArgs).not.toContain("PLAINTEXT");
    } finally {
      consoleInfoSpy.mockRestore();
    }
  });

  it("회전 클릭 → RotateConfirmModal 오픈, target 이 표시된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SecretsPage />);
    // 첫 번째 시크릿(anthropic) 의 회전 버튼 클릭.
    const row = screen.getByTestId("secret-row-keyring:llm.anthropic_api_key");
    const rotateBtn = row.querySelector(
      "button:nth-last-child(1)",
    ) as HTMLButtonElement;
    expect(rotateBtn.textContent).toBe("회전");
    fireEvent.click(rotateBtn);
    expect(screen.getByTestId("rotate-confirm-modal")).toBeDefined();
    expect(screen.getByTestId("rotate-confirm-target").textContent).toContain(
      "llm.anthropic_api_key",
    );
  });

  it("reveal/copy 콜백은 console 에 *키 ID* 만 박제 — 평문 prop 이 존재하지 않음", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    const consoleInfoSpy = vi
      .spyOn(console, "info")
      .mockImplementation(() => {});
    try {
      render(<SecretsPage />);
      const row = screen.getByTestId(
        "secret-row-keyring:llm.anthropic_api_key",
      );
      const buttons = row.querySelectorAll("button");
      // 보기 / 복사 / 회전 순으로 SecretField 가 렌더링.
      const reveal = Array.from(buttons).find((b) => b.textContent === "보기");
      const copy = Array.from(buttons).find((b) => b.textContent === "복사");
      fireEvent.click(reveal!);
      fireEvent.click(copy!);

      const allArgs = consoleInfoSpy.mock.calls.flat().map(String).join(" ");
      expect(allArgs).toContain("[secrets] reveal request");
      expect(allArgs).toContain("[secrets] copy request");
      expect(allArgs).toContain("keyring:llm.anthropic_api_key");
    } finally {
      consoleInfoSpy.mockRestore();
    }
  });
});
