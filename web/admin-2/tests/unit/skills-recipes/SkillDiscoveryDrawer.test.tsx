/**
 * SkillDiscoveryDrawer 단위 테스트 — 검색 / 카탈로그 그룹 / 추가 / 설치됨 비활성.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SkillDiscoveryDrawer } from "@/app/(shell)/skills-recipes/_components/SkillDiscoveryDrawer";
import { CATALOG } from "./_fixture";

describe("SkillDiscoveryDrawer", () => {
  it("open=false 면 dialog 렌더 안 됨", () => {
    render(
      <SkillDiscoveryDrawer
        open={false}
        catalog={CATALOG}
        onClose={() => {}}
        onAdd={() => {}}
      />,
    );
    expect(screen.queryByTestId("skill-discovery-drawer")).toBeNull();
  });

  it("open=true 면 카탈로그 카드들과 검색 입력 노출", () => {
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        onClose={() => {}}
        onAdd={() => {}}
      />,
    );
    expect(screen.getByTestId("skill-discovery-drawer")).toBeDefined();
    expect(screen.getByTestId("skill-discovery-search")).toBeDefined();
    expect(screen.getByTestId("catalog-skill-gmail-skill")).toBeDefined();
    expect(screen.getByTestId("catalog-skill-weather-skill")).toBeDefined();
  });

  it("검색 입력에 키워드를 넣으면 필터링", () => {
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        onClose={() => {}}
        onAdd={() => {}}
      />,
    );
    const search = screen.getByTestId(
      "skill-discovery-search",
    ) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "weather" } });
    expect(screen.queryByTestId("catalog-skill-gmail-skill")).toBeNull();
    expect(screen.getByTestId("catalog-skill-weather-skill")).toBeDefined();
  });

  it("검색 결과가 0 이면 empty state 노출", () => {
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        onClose={() => {}}
        onAdd={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("skill-discovery-search"), {
      target: { value: "nonexistent-zzz" },
    });
    expect(screen.getByTestId("skill-discovery-empty")).toBeDefined();
  });

  it("'추가' 버튼은 onAdd(catalogSkill) 호출 — 미설치 항목만", () => {
    const onAdd = vi.fn();
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        onClose={() => {}}
        onAdd={onAdd}
      />,
    );
    fireEvent.click(screen.getByTestId("catalog-skill-weather-skill-add"));
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd.mock.calls[0]?.[0].id).toBe("weather-skill");
  });

  it("이미 설치된 항목의 추가 버튼은 disabled", () => {
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        onClose={() => {}}
        onAdd={() => {}}
      />,
    );
    const btn = screen.getByTestId(
      "catalog-skill-gmail-skill-add",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("installedIds prop 으로도 '설치됨' 처리됨", () => {
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        installedIds={["weather-skill"]}
        onClose={() => {}}
        onAdd={() => {}}
      />,
    );
    const btn = screen.getByTestId(
      "catalog-skill-weather-skill-add",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("닫기 버튼 클릭 시 onClose 호출", () => {
    const onClose = vi.fn();
    render(
      <SkillDiscoveryDrawer
        open
        catalog={CATALOG}
        onClose={onClose}
        onAdd={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("skill-discovery-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
