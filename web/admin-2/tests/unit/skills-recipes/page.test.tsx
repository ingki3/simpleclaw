/**
 * /skills-recipes 페이지 통합 단위 테스트 — 섹션 렌더 + 4-variant + 모달/드로어 트리거.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/skills-recipes",
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

import SkillsRecipesPage from "@/app/(shell)/skills-recipes/page";

describe("SkillsRecipesPage", () => {
  it("h1 + skills + recipes 섹션을 모두 렌더한다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SkillsRecipesPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: "스킬 & 레시피" }),
    ).toBeDefined();
    expect(screen.getByTestId("skills-list")).toBeDefined();
    expect(screen.getByTestId("recipes-list")).toBeDefined();
    expect(screen.getByTestId("skills-recipes-search")).toBeDefined();
  });

  it("?skills=loading 면 SkillsList 가 loading variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("skills=loading"));
    render(<SkillsRecipesPage />);
    expect(
      screen.getByTestId("skills-list").getAttribute("data-state"),
    ).toBe("loading");
    expect(screen.getByTestId("skills-list-loading")).toBeDefined();
  });

  it("?skills=empty 면 SkillsList 가 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("skills=empty"));
    render(<SkillsRecipesPage />);
    expect(
      screen.getByTestId("skills-list").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("skills-list-empty")).toBeDefined();
  });

  it("?skills=error 면 SkillsList 가 error variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("skills=error"));
    render(<SkillsRecipesPage />);
    expect(
      screen.getByTestId("skills-list").getAttribute("data-state"),
    ).toBe("error");
    expect(screen.getByTestId("skills-list-error")).toBeDefined();
  });

  it("?recipes=empty 면 RecipesList 가 empty variant", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams("recipes=empty"));
    render(<SkillsRecipesPage />);
    expect(
      screen.getByTestId("recipes-list").getAttribute("data-state"),
    ).toBe("empty");
    expect(screen.getByTestId("recipes-list-empty")).toBeDefined();
  });

  it("'카탈로그 열기' 클릭 시 SkillDiscoveryDrawer 가 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SkillsRecipesPage />);
    fireEvent.click(screen.getByTestId("skills-recipes-discover"));
    expect(screen.getByTestId("skill-discovery-drawer")).toBeDefined();
  });

  it("스킬 카드 '정책 편집' 클릭 시 RetryPolicyModal 이 열린다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SkillsRecipesPage />);
    fireEvent.click(screen.getByTestId("skill-card-gmail-skill-edit-policy"));
    expect(screen.getByTestId("retry-policy-modal")).toBeDefined();
  });

  it("검색 입력에 키워드를 넣으면 스킬이 필터링된다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SkillsRecipesPage />);
    fireEvent.change(screen.getByTestId("skills-recipes-search"), {
      target: { value: "calendar" },
    });
    expect(
      screen.queryByTestId("skill-card-gmail-skill"),
    ).toBeNull();
    expect(
      screen.getByTestId("skill-card-google-calendar-skill"),
    ).toBeDefined();
  });

  it("Discovery 에서 미설치 스킬 추가 시 SkillsList 에 카드가 새로 생긴다", () => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    render(<SkillsRecipesPage />);
    fireEvent.click(screen.getByTestId("skills-recipes-discover"));
    fireEvent.click(screen.getByTestId("catalog-skill-weather-skill-add"));
    expect(screen.getByTestId("skill-card-weather-skill")).toBeDefined();
  });
});
