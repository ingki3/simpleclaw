/**
 * Skills & Recipes e2e (BIZ-117) — 헤더, 4-variant, Discovery Drawer, Retry Policy 모달.
 */
import { expect, test } from "@playwright/test";

test("Skills & Recipes — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/skills-recipes");

  await expect(
    page.getByRole("heading", { level: 1, name: "스킬 & 레시피" }),
  ).toBeVisible();

  await expect(page.getByTestId("skills-list")).toBeVisible();
  await expect(page.getByTestId("recipes-list")).toBeVisible();
  await expect(page.getByTestId("skills-recipes-search")).toBeVisible();

  // 기본 fixture — gmail / google-calendar / us-stock 등 스킬 카드.
  await expect(page.getByTestId("skill-card-gmail-skill")).toBeVisible();
  await expect(page.getByTestId("recipe-card-morning-briefing")).toBeVisible();
});

test.describe("SkillsList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?skills=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/skills-recipes?skills=${state}`);
      const list = page.getByTestId("skills-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant 은 aria-busy='true' + 스켈레톤", async ({ page }) => {
    await page.goto("/skills-recipes?skills=loading");
    const list = page.getByTestId("skills-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("skills-list-loading")).toBeVisible();
  });

  test("error variant 은 alert role", async ({ page }) => {
    await page.goto("/skills-recipes?skills=error");
    const err = page.getByTestId("skills-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test.describe("RecipesList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?recipes=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/skills-recipes?recipes=${state}`);
      const list = page.getByTestId("recipes-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }
});

test("'카탈로그 열기' → Discovery Drawer 가 열린다", async ({ page }) => {
  await page.goto("/skills-recipes");
  await page.getByTestId("skills-recipes-discover").click();
  await expect(page.getByTestId("skill-discovery-drawer")).toBeVisible();
  // 검색 입력에 키워드 — 결과가 즉시 좁혀진다.
  await page.getByTestId("skill-discovery-search").fill("weather");
  await expect(page.getByTestId("catalog-skill-weather-skill")).toBeVisible();
  await expect(page.getByTestId("catalog-skill-gmail-skill")).toBeHidden();
  await page.getByTestId("skill-discovery-close").click();
  await expect(page.getByTestId("skill-discovery-drawer")).toBeHidden();
});

test("Discovery 에서 미설치 스킬 추가 → SkillsList 에 카드 추가", async ({
  page,
}) => {
  await page.goto("/skills-recipes");
  await page.getByTestId("skills-recipes-discover").click();
  await page.getByTestId("catalog-skill-weather-skill-add").click();
  // Drawer 가 열린 상태에서도, 좌측 SkillsList 에 새 카드가 추가됨.
  await expect(page.getByTestId("skill-card-weather-skill")).toBeVisible();
});

test("스킬 카드 '정책 편집' → RetryPolicyModal prefill", async ({ page }) => {
  await page.goto("/skills-recipes");
  await page.getByTestId("skill-card-gmail-skill-edit-policy").click();
  await expect(page.getByTestId("retry-policy-modal")).toBeVisible();
  // 헤더에 스킬 이름 노출.
  await expect(page.getByTestId("retry-policy-modal")).toContainText(
    "gmail-skill",
  );
  // prefill — fixture 의 maxAttempts=3.
  await expect(page.getByTestId("retry-policy-max-attempts")).toHaveValue("3");
  await page.getByTestId("retry-policy-cancel").click();
  await expect(page.getByTestId("retry-policy-modal")).toBeHidden();
});

test("Retry Policy 검증 실패 — maxAttempts=0 면 저장 차단", async ({
  page,
}) => {
  await page.goto("/skills-recipes");
  await page.getByTestId("skill-card-gmail-skill-edit-policy").click();
  await page.getByTestId("retry-policy-max-attempts").fill("0");
  await page.getByTestId("retry-policy-submit").click();
  await expect(
    page.getByTestId("retry-policy-max-attempts-error"),
  ).toBeVisible();
});
