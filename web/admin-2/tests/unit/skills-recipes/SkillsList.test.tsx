/**
 * SkillsList 단위 테스트 — 4-variant + 빈 결과 분기 + 콜백.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SkillsList } from "@/app/(shell)/skills-recipes/_components/SkillsList";
import { SKILL } from "./_fixture";

describe("SkillsList", () => {
  it("default state — 카드 그리드 + Switch + 정책 버튼", () => {
    const onToggle = vi.fn();
    const onEditPolicy = vi.fn();
    render(
      <SkillsList
        state="default"
        skills={[SKILL]}
        onToggleEnabled={onToggle}
        onEditPolicy={onEditPolicy}
        onDiscover={() => {}}
      />,
    );
    const list = screen.getByTestId("skills-list");
    expect(list.getAttribute("data-state")).toBe("default");
    expect(screen.getByTestId(`skill-card-${SKILL.id}`)).toBeDefined();
    expect(screen.getByTestId("skills-list-grid")).toBeDefined();
  });

  it("default + skills=[] + 검색어 → filtered empty", () => {
    render(
      <SkillsList
        state="default"
        skills={[]}
        searchQuery="abc"
        onToggleEnabled={() => {}}
        onEditPolicy={() => {}}
        onDiscover={() => {}}
      />,
    );
    const empty = screen.getByTestId("skills-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("filtered");
  });

  it("default + skills=[] + 검색어 없음 → empty (none)", () => {
    render(
      <SkillsList
        state="default"
        skills={[]}
        onToggleEnabled={() => {}}
        onEditPolicy={() => {}}
        onDiscover={() => {}}
      />,
    );
    const empty = screen.getByTestId("skills-list-empty");
    expect(empty.getAttribute("data-empty-reason")).toBe("none");
  });

  it("loading state — aria-busy + 스켈레톤", () => {
    render(
      <SkillsList
        state="loading"
        onToggleEnabled={() => {}}
        onEditPolicy={() => {}}
        onDiscover={() => {}}
      />,
    );
    expect(
      screen.getByTestId("skills-list").getAttribute("aria-busy"),
    ).toBe("true");
    expect(screen.getByTestId("skills-list-loading")).toBeDefined();
  });

  it("empty state — 카탈로그 CTA 가 onDiscover 를 호출", () => {
    const onDiscover = vi.fn();
    render(
      <SkillsList
        state="empty"
        onToggleEnabled={() => {}}
        onEditPolicy={() => {}}
        onDiscover={onDiscover}
      />,
    );
    fireEvent.click(screen.getByText("카탈로그 열기"));
    expect(onDiscover).toHaveBeenCalledTimes(1);
  });

  it("error state — alert role + retry 호출", () => {
    const onRetry = vi.fn();
    render(
      <SkillsList
        state="error"
        errorMessage="오류"
        onRetry={onRetry}
        onToggleEnabled={() => {}}
        onEditPolicy={() => {}}
        onDiscover={() => {}}
      />,
    );
    const err = screen.getByTestId("skills-list-error");
    expect(err.getAttribute("role")).toBe("alert");
    fireEvent.click(screen.getByTestId("skills-list-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("카드 Switch 클릭 시 onToggleEnabled 가 (id, !enabled) 와 함께 호출", () => {
    const onToggle = vi.fn();
    render(
      <SkillsList
        state="default"
        skills={[SKILL]}
        onToggleEnabled={onToggle}
        onEditPolicy={() => {}}
        onDiscover={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId(`skill-card-${SKILL.id}-toggle`));
    expect(onToggle).toHaveBeenCalledWith(SKILL.id, !SKILL.enabled);
  });

  it("카드 '정책 편집' 클릭 시 onEditPolicy 가 skill 과 함께 호출", () => {
    const onEditPolicy = vi.fn();
    render(
      <SkillsList
        state="default"
        skills={[SKILL]}
        onToggleEnabled={() => {}}
        onEditPolicy={onEditPolicy}
        onDiscover={() => {}}
      />,
    );
    fireEvent.click(
      screen.getByTestId(`skill-card-${SKILL.id}-edit-policy`),
    );
    expect(onEditPolicy).toHaveBeenCalledWith(SKILL);
  });
});
