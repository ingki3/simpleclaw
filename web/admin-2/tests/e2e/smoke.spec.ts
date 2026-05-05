/**
 * Playwright 스모크 e2e — Admin 2.0 hello-world 가 실제 브라우저에서 뜨는지 확인.
 *
 * S0 의 DoD ("dev server 가 hello-world 페이지를 렌더") 를 e2e 단에서도 입증한다.
 */
import { expect, test } from "@playwright/test";

test("hello-world 페이지가 렌더된다", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /SimpleClaw Admin 2\.0/i }),
  ).toBeVisible();
  await expect(page.getByTestId("scaffold-marker")).toContainText("BIZ-111");
});
