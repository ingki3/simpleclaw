/**
 * 스모크 단위 테스트 — Admin 2.0 hello-world 페이지가 렌더되는지 확인.
 *
 * S0 의 목적은 빌드/테스트 인프라가 살아 있다는 것을 입증하는 것이므로,
 * 본 테스트는 페이지 컴포넌트가 예외 없이 렌더되고 scaffold marker 가 보이는지만 검증한다.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import HomePage from "@/app/page";

describe("Admin 2.0 hello-world 페이지", () => {
  it("scaffold marker 와 제목을 렌더한다", () => {
    render(<HomePage />);
    expect(
      screen.getByRole("heading", { name: /SimpleClaw Admin 2\.0/i }),
    ).toBeDefined();
    expect(screen.getByTestId("scaffold-marker").textContent).toContain(
      "BIZ-111",
    );
  });
});
