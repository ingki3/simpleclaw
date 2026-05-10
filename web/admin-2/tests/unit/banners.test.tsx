/**
 * NotConnectedBanner 페이지 통합 — BIZ-151 sub-1.
 *
 * 11개 영역 중 fixture 운영 모드인 10개 페이지 (dashboard / llm-router /
 * skills-recipes / cron / memory / secrets / channels / logging / audit /
 * system) 가 헤더 직하에 "데몬 API 연결 대기" 배너를 노출하는지 확인한다.
 *
 * persona 는 아직 AreaPlaceholder 스텁(S5 예정)이므로 placeholder 자체가
 * 안내 역할을 겸하고 별도의 배너는 두지 않는다.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const mockSearchParams = vi.fn(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  usePathname: () => "/",
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

import { ThemeProvider } from "@/design/ThemeProvider";
import DashboardPage from "@/app/(shell)/dashboard/page";
import LlmRouterPage from "@/app/(shell)/llm-router/page";
import SkillsRecipesPage from "@/app/(shell)/skills-recipes/page";
import CronPage from "@/app/(shell)/cron/page";
import MemoryPage from "@/app/(shell)/memory/page";
import SecretsPage from "@/app/(shell)/secrets/page";
import ChannelsPage from "@/app/(shell)/channels/page";
import LoggingPage from "@/app/(shell)/logging/page";
import AuditPage from "@/app/(shell)/audit/page";
import SystemPage from "@/app/(shell)/system/page";

const PAGES: ReadonlyArray<readonly [string, () => React.JSX.Element]> = [
  ["dashboard", DashboardPage],
  ["llm-router", LlmRouterPage],
  ["skills-recipes", SkillsRecipesPage],
  ["cron", CronPage],
  ["memory", MemoryPage],
  ["secrets", SecretsPage],
  ["channels", ChannelsPage],
  ["logging", LoggingPage],
  ["audit", AuditPage],
  ["system", SystemPage],
];

describe("NotConnectedBanner — fixture 운영 10개 페이지", () => {
  it.each(PAGES)("%s 페이지는 데몬 API 연결 대기 배너를 노출한다", (_name, Page) => {
    mockSearchParams.mockReturnValueOnce(new URLSearchParams());
    // ThemeProvider 는 system 페이지의 ThemeCard 가 useTheme 을 요구하기 때문에 필수.
    // 다른 페이지에는 부수효과가 없으므로 일률 적용.
    render(
      <ThemeProvider>
        <Page />
      </ThemeProvider>,
    );
    const banner = screen.getByTestId("not-connected-banner");
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("데몬 API 연결 대기");
  });
});
