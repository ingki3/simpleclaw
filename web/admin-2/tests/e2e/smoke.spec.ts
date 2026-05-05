/**
 * Playwright 스모크 e2e — Admin 2.0 App Shell 의 기본 진입(BIZ-113 이후).
 *
 * 루트 `/` 진입 → `/dashboard` 리다이렉트 → Sidebar/Topbar/AreaPlaceholder 렌더.
 */
import { expect, test } from "@playwright/test";

test("루트 진입 시 /dashboard 셸이 렌더된다", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByTestId("sidebar")).toBeVisible();
  await expect(page.getByTestId("topbar")).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: "대시보드" }),
  ).toBeVisible();
});
