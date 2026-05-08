/**
 * Cron e2e (BIZ-118) — 헤더, 4-variant, 잡 토글, NewCronJobModal 검증/생성.
 */
import { expect, test } from "@playwright/test";

test("Cron — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/cron");

  await expect(
    page.getByRole("heading", { level: 1, name: "크론" }),
  ).toBeVisible();

  await expect(page.getByTestId("cron-jobs-list")).toBeVisible();
  await expect(page.getByTestId("cron-history")).toBeVisible();
  await expect(page.getByTestId("cron-search")).toBeVisible();
  await expect(page.getByTestId("cron-jobs-table")).toBeVisible();

  // 기본 fixture — dreaming.cycle / memory.compact / reflection.weekly 등.
  await expect(
    page.getByTestId("cron-job-dreaming-cycle-toggle"),
  ).toBeVisible();
});

test.describe("CronJobsList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?jobs=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/cron?jobs=${state}`);
      const list = page.getByTestId("cron-jobs-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant — aria-busy='true' + 스켈레톤", async ({ page }) => {
    await page.goto("/cron?jobs=loading");
    const list = page.getByTestId("cron-jobs-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("cron-jobs-list-loading")).toBeVisible();
  });

  test("error variant — alert role", async ({ page }) => {
    await page.goto("/cron?jobs=error");
    const err = page.getByTestId("cron-jobs-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test("`+ 새 작업` → NewCronJobModal 이 열리고 DryRun 미리보기 노출", async ({
  page,
}) => {
  await page.goto("/cron");
  await page.getByTestId("cron-create").click();
  await expect(page.getByTestId("new-cron-job-modal")).toBeVisible();
  // 기본 표현식이 유효 → DryRun 카드가 보인다.
  await expect(page.getByTestId("new-cron-job-dry-run")).toBeVisible();
  await page.getByTestId("new-cron-job-cancel").click();
  await expect(page.getByTestId("new-cron-job-modal")).toBeHidden();
});

test("Cron 표현식이 잘못되면 생성 차단 + 에러 라인 노출", async ({ page }) => {
  await page.goto("/cron");
  await page.getByTestId("cron-create").click();
  await page.getByTestId("new-cron-job-name").fill("bad-job");
  await page.getByTestId("new-cron-job-schedule").fill("invalid");
  await page.getByTestId("new-cron-job-submit").click();
  await expect(page.getByTestId("new-cron-job-schedule-error")).toBeVisible();
});

test("정상 입력 → 새 잡이 목록에 추가된다", async ({ page }) => {
  await page.goto("/cron");
  await page.getByTestId("cron-create").click();
  await page.getByTestId("new-cron-job-name").fill("e2e-fresh");
  await page.getByTestId("new-cron-job-schedule").fill("0 4 * * *");
  await page.getByTestId("new-cron-job-submit").click();
  await expect(page.getByTestId("new-cron-job-modal")).toBeHidden();
  await expect(page.getByTestId("cron-job-e2e-fresh-toggle")).toBeVisible();
});

test("Switch 토글 — aria-checked 가 즉시 갱신", async ({ page }) => {
  await page.goto("/cron");
  const toggle = page.getByTestId("cron-job-dreaming-cycle-toggle");
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
});

test("검색 — 키워드로 잡 필터링", async ({ page }) => {
  await page.goto("/cron");
  await page.getByTestId("cron-search").fill("memory");
  await expect(
    page.getByTestId("cron-job-memory-compact-toggle"),
  ).toBeVisible();
  await expect(
    page.getByTestId("cron-job-dreaming-cycle-toggle"),
  ).toBeHidden();
});
