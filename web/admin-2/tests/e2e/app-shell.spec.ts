/**
 * App Shell e2e (BIZ-113) — 11개 영역 라우트 nav · ⌘K 동작 · 다크 토글 페르시스턴스.
 */
import { expect, test } from "@playwright/test";

const AREAS = [
  { path: "/dashboard", label: "대시보드" },
  { path: "/llm-router", label: "LLM 라우터" },
  { path: "/persona", label: "페르소나" },
  { path: "/skills-recipes", label: "스킬 & 레시피" },
  { path: "/cron", label: "크론" },
  { path: "/memory", label: "기억" },
  { path: "/secrets", label: "시크릿" },
  { path: "/channels", label: "채널" },
  { path: "/logging", label: "로그" },
  { path: "/audit", label: "감사" },
  { path: "/system", label: "시스템" },
];

for (const area of AREAS) {
  test(`Sidebar nav → ${area.label} (${area.path})`, async ({ page }) => {
    await page.goto("/dashboard");
    const link = page.getByTestId(`sidebar-link-${area.path.slice(1)}`);
    await link.click();
    await expect(page).toHaveURL(new RegExp(`${area.path}$`));
    await expect(
      page.getByRole("heading", { level: 1, name: area.label }),
    ).toBeVisible();
    // active 표시
    await expect(link).toHaveAttribute("aria-current", "page");
  });
}

test("⌘K Command Palette 가 열리고 영역 점프", async ({ page }) => {
  await page.goto("/dashboard");
  await page.keyboard.press("Meta+K");
  const palette = page.getByTestId("command-palette");
  await expect(palette).toBeVisible();

  await page.getByTestId("command-palette-input").fill("cron");
  await expect(page.getByTestId("command-palette-item-cron")).toBeVisible();
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/\/cron$/);
  await expect(palette).toBeHidden();
});

test("⌘K — Ctrl+K 로도 열린다 (Linux/Win 호환)", async ({ page }) => {
  await page.goto("/dashboard");
  await page.keyboard.press("Control+K");
  await expect(page.getByTestId("command-palette")).toBeVisible();
});

test("ESC 로 Command Palette 가 닫힌다", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByTestId("topbar-palette-trigger").click();
  await expect(page.getByTestId("command-palette")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("command-palette")).toBeHidden();
});

test("다크 모드 토글이 새로고침 후에도 유지된다", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByTestId("theme-toggle-dark").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  // 시스템으로 되돌리면 attribute 가 제거되고 localStorage 에는 system 이 남는다.
  await page.getByTestId("theme-toggle-system").click();
  await page.reload();
  await expect(
    await page.evaluate(() =>
      window.localStorage.getItem("simpleclaw.admin2.theme"),
    ),
  ).toBe("system");
});

test("Topbar breadcrumb 가 현재 영역 라벨을 노출한다", async ({ page }) => {
  await page.goto("/secrets");
  await expect(page.getByTestId("topbar-breadcrumb")).toContainText("시크릿");
});
