/**
 * /design-system 카탈로그 페이지 — 모든 reusable 그룹 헤더가 한 페이지에 노출되는지 확인.
 *
 * BIZ-112 DoD: "Storybook 또는 preview 라우트에서 26개 모두 렌더 (Light/Dark 양쪽)".
 * 본 테스트는 그룹 헤더 카운트로 인벤토리 누락을 차단한다.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import DesignSystemPage from "@/app/design-system/page";
import { ThemeProvider } from "@/design/ThemeProvider";

describe("/design-system catalog", () => {
  it("Atomic / Molecular / Domain 섹션과 26+ 그룹 헤더를 그린다", () => {
    render(
      <ThemeProvider>
        <DesignSystemPage />
      </ThemeProvider>,
    );

    // 섹션 헤더.
    expect(screen.getByRole("heading", { name: /Atomic/ })).toBeDefined();
    expect(screen.getByRole("heading", { name: /Molecular/ })).toBeDefined();
    expect(screen.getByRole("heading", { name: /Domain/ })).toBeDefined();

    // 카탈로그 그룹 — DESIGN.md §3.4 도메인 컴포넌트 5종 헤더.
    expect(screen.getByText("CronJobRow")).toBeDefined();
    expect(screen.getByText("PersonaEditor")).toBeDefined();
    expect(screen.getByText("WebhookGuardCard")).toBeDefined();
    expect(screen.getByText("TraceTimeline")).toBeDefined();
    expect(screen.getByText("MemoryClusterMap")).toBeDefined();

    // Theme 토글 라디오 그룹.
    expect(screen.getByRole("radiogroup", { name: "theme" })).toBeDefined();
  });
});
