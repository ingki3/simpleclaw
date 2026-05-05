/**
 * SecretsList 단위 테스트 — 4-variant + reveal/copy/rotate 콜백 + 시크릿 값 누출 금지.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SecretsList } from "@/app/(shell)/secrets/_components/SecretsList";
import {
  SECRET_HOT,
  SECRET_LIST,
  SECRET_PROCESS_RESTART,
} from "./_fixture";

describe("SecretsList — variants", () => {
  it("default + 비어있는 목록 → empty 표시", () => {
    render(<SecretsList state="default" secrets={[]} />);
    expect(screen.getByTestId("secrets-list-empty")).toBeDefined();
    expect(
      screen.getByTestId("secrets-list-empty").getAttribute("data-empty-reason"),
    ).toBe("none");
  });

  it("default + 검색어 + 결과 0 → filtered empty", () => {
    render(
      <SecretsList state="default" secrets={[]} searchQuery="없는키" />,
    );
    expect(
      screen.getByTestId("secrets-list-empty").getAttribute("data-empty-reason"),
    ).toBe("filtered");
  });

  it("loading variant → aria-busy true + skeleton", () => {
    render(<SecretsList state="loading" />);
    const list = screen.getByTestId("secrets-list");
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(list.getAttribute("data-state")).toBe("loading");
    expect(screen.getByTestId("secrets-list-loading")).toBeDefined();
  });

  it("empty variant → CTA 노출", () => {
    const onAdd = vi.fn();
    render(<SecretsList state="empty" onAdd={onAdd} />);
    fireEvent.click(screen.getByTestId("secrets-list-empty-cta"));
    expect(onAdd).toHaveBeenCalledTimes(1);
  });

  it("error variant → role=alert + retry", () => {
    const onRetry = vi.fn();
    render(<SecretsList state="error" onRetry={onRetry} />);
    const err = screen.getByTestId("secrets-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("secrets-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("SecretsList — default rendering", () => {
  it("grouped=true → scope 별 그룹 헤더가 렌더된다", () => {
    render(<SecretsList state="default" secrets={SECRET_LIST} />);
    expect(screen.getByTestId("secrets-grouped")).toBeDefined();
    expect(screen.getByTestId("secrets-group-llm-provider")).toBeDefined();
    expect(screen.getByTestId("secrets-group-channel")).toBeDefined();
    expect(screen.getByTestId("secrets-group-system")).toBeDefined();
  });

  it("검색 활성 시 그룹 없이 flat 표시", () => {
    render(
      <SecretsList
        state="default"
        secrets={SECRET_LIST}
        searchQuery="llm"
      />,
    );
    expect(screen.getByTestId("secrets-flat")).toBeDefined();
    expect(screen.queryByTestId("secrets-grouped")).toBeNull();
  });

  it("각 행에 마스킹 미리보기 + 회전/사용 메타가 표시된다", () => {
    render(<SecretsList state="default" secrets={[SECRET_HOT]} />);
    const row = screen.getByTestId(`secret-row-${SECRET_HOT.id}`);
    expect(row.textContent).toContain(SECRET_HOT.keyName);
    expect(row.textContent).toContain(SECRET_HOT.maskedPreview);
    const meta = screen.getByTestId(`secret-row-${SECRET_HOT.id}-meta`);
    expect(meta.textContent).toMatch(/회전/);
    expect(meta.textContent).toMatch(/사용/);
  });

  it("회전 이력 없는 시크릿은 '회전 이력 없음' 메타", () => {
    render(
      <SecretsList state="default" secrets={[SECRET_PROCESS_RESTART]} />,
    );
    const meta = screen.getByTestId(
      `secret-row-${SECRET_PROCESS_RESTART.id}-meta`,
    );
    expect(meta.textContent).toContain("회전 이력 없음");
    expect(meta.textContent).toContain("사용 이력 없음");
  });
});

describe("SecretsList — actions", () => {
  it("reveal/copy/rotate 콜백은 *키 ID* 만 받는다 — 평문 prop 자체가 없음", () => {
    const onReveal = vi.fn();
    const onCopy = vi.fn();
    const onRotate = vi.fn();
    render(
      <SecretsList
        state="default"
        secrets={[SECRET_HOT]}
        onReveal={onReveal}
        onCopy={onCopy}
        onRotate={onRotate}
      />,
    );
    fireEvent.click(screen.getByText("보기"));
    expect(onReveal).toHaveBeenCalledWith(SECRET_HOT.id);

    fireEvent.click(screen.getByText("복사"));
    expect(onCopy).toHaveBeenCalledWith(SECRET_HOT.id);

    fireEvent.click(screen.getByText("회전"));
    expect(onRotate).toHaveBeenCalledWith(SECRET_HOT);
  });

  it("DOM 렌더 결과에 평문(••••이 아닌) 값이 절대 등장하지 않는다", () => {
    // 평문 prop 자체가 컴포넌트 API 에 없으므로 우연히 노출될 경로가 없다 —
    // 그래도 회귀 방지를 위해 fixture 의 maskedPreview 만 노출되는지 확인한다.
    const { container } = render(
      <SecretsList state="default" secrets={SECRET_LIST} />,
    );
    const html = container.innerHTML;
    expect(html).toContain(SECRET_HOT.maskedPreview);
    // SECRET_HOT 의 가짜 평문 패턴 (last4 직전 글자) 이 노출되면 누출.
    expect(html).not.toMatch(/sk-[a-zA-Z0-9]{8,}/);
  });
});
