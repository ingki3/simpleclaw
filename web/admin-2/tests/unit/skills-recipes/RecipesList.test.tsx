/**
 * RecipesList 단위 테스트 — 4-variant + Switch 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { RecipesList } from "@/app/(shell)/skills-recipes/_components/RecipesList";
import { RECIPE } from "./_fixture";

describe("RecipesList", () => {
  it("default state — 카드 그리드", () => {
    render(
      <RecipesList
        state="default"
        recipes={[RECIPE]}
        onToggleEnabled={() => {}}
      />,
    );
    expect(screen.getByTestId("recipes-list-grid")).toBeDefined();
    expect(screen.getByTestId(`recipe-card-${RECIPE.id}`)).toBeDefined();
  });

  it("default + recipes=[] + 검색어 → filtered empty", () => {
    render(
      <RecipesList
        state="default"
        recipes={[]}
        searchQuery="x"
        onToggleEnabled={() => {}}
      />,
    );
    expect(
      screen
        .getByTestId("recipes-list-empty")
        .getAttribute("data-empty-reason"),
    ).toBe("filtered");
  });

  it("loading state — aria-busy", () => {
    render(<RecipesList state="loading" onToggleEnabled={() => {}} />);
    expect(
      screen.getByTestId("recipes-list").getAttribute("aria-busy"),
    ).toBe("true");
    expect(screen.getByTestId("recipes-list-loading")).toBeDefined();
  });

  it("empty state — EmptyState (none)", () => {
    render(<RecipesList state="empty" onToggleEnabled={() => {}} />);
    expect(
      screen
        .getByTestId("recipes-list-empty")
        .getAttribute("data-empty-reason"),
    ).toBe("none");
  });

  it("error state — alert role + retry", () => {
    const onRetry = vi.fn();
    render(
      <RecipesList
        state="error"
        onRetry={onRetry}
        onToggleEnabled={() => {}}
      />,
    );
    expect(
      screen.getByTestId("recipes-list-error").getAttribute("role"),
    ).toBe("alert");
    fireEvent.click(screen.getByTestId("recipes-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("Switch 클릭 시 onToggleEnabled 가 (id, !enabled) 호출", () => {
    const onToggle = vi.fn();
    render(
      <RecipesList
        state="default"
        recipes={[RECIPE]}
        onToggleEnabled={onToggle}
      />,
    );
    fireEvent.click(screen.getByTestId(`recipe-card-${RECIPE.id}-toggle`));
    expect(onToggle).toHaveBeenCalledWith(RECIPE.id, !RECIPE.enabled);
  });
});
