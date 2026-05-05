/**
 * Playwright 스모크 e2e — /design-system 카탈로그가 실제 브라우저에서 뜨는지 확인.
 *
 * BIZ-112 DoD: "Storybook 또는 preview 라우트에서 26개 모두 렌더 (Light/Dark 양쪽)".
 * 본 스모크는 페이지 진입 + theme 토글이 <html data-theme> 에 반영되는지만 검증한다.
 */
import { expect, test } from "@playwright/test";

test("/design-system 카탈로그가 렌더되고 다크 모드 토글이 반영된다", async ({
  page,
}) => {
  await page.goto("/design-system");

  // 섹션 헤더 3종이 모두 노출.
  await expect(
    page.getByRole("heading", { name: /Atomic/, level: 2 }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Molecular/, level: 2 }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Domain/, level: 2 }),
  ).toBeVisible();

  // 다크 모드 토글 — <html data-theme="dark"> 가 셋업.
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  // 라이트 모드 — attribute 가 light 로 갱신.
  await page.getByRole("radio", { name: "Light" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
