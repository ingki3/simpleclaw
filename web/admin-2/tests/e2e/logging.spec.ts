/**
 * Logging e2e (BIZ-122) — 헤더 + 4-variant + 4개 필터 + 행 → Trace Detail.
 */
import { expect, test } from "@playwright/test";

test("Logging — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/logging");

  await expect(
    page.getByRole("heading", { level: 1, name: "로그" }),
  ).toBeVisible();

  await expect(page.getByTestId("traces-list")).toBeVisible();
  await expect(page.getByTestId("traces-table")).toBeVisible();
  await expect(page.getByTestId("logging-search")).toBeVisible();
  await expect(page.getByTestId("logging-level")).toBeVisible();
  await expect(page.getByTestId("logging-range")).toBeVisible();
  await expect(page.getByTestId("logging-service")).toBeVisible();
});

test.describe("TracesList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?traces=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/logging?traces=${state}`);
      const list = page.getByTestId("traces-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant — aria-busy='true' + 스켈레톤", async ({ page }) => {
    await page.goto("/logging?traces=loading");
    const list = page.getByTestId("traces-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("traces-list-loading")).toBeVisible();
  });

  test("error variant — alert role", async ({ page }) => {
    await page.goto("/logging?traces=error");
    const err = page.getByTestId("traces-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test("검색 — 메시지 하이라이트가 노출된다", async ({ page }) => {
  await page.goto("/logging");
  await page.getByTestId("logging-search").fill("claude");
  // claude 가 들어간 메시지의 mark 가 화면에 보인다.
  const marks = page.getByTestId("traces-highlight").first();
  await expect(marks).toBeVisible();
  await expect(marks).toHaveText(/claude/i);
});

test("레벨 필터 — error 만 남기면 cron error 행이 노출된다", async ({ page }) => {
  await page.goto("/logging");
  await page.getByTestId("logging-level").selectOption("error");
  await expect(page.getByTestId("traces-row-evt-7")).toBeVisible();
  await expect(page.getByTestId("traces-row-evt-1")).toBeHidden();
});

test("서비스 필터 — cron 만 남긴다", async ({ page }) => {
  await page.goto("/logging");
  await page.getByTestId("logging-service").selectOption("cron");
  await expect(page.getByTestId("traces-row-evt-6")).toBeVisible();
  await expect(page.getByTestId("traces-row-evt-1")).toBeHidden();
});

test("trace 행 클릭 → TraceDetailModal 이 열린다", async ({ page }) => {
  await page.goto("/logging");
  await page.getByTestId("traces-row-evt-10").click();
  await expect(page.getByTestId("trace-detail-modal")).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 2, name: "channel.telegram.dispatch" }),
  ).toBeVisible();
  await expect(page.getByTestId("trace-detail-summary")).toBeVisible();
  await expect(page.getByTestId("trace-detail-span-inspector")).toBeVisible();
  await expect(page.getByTestId("trace-detail-raw")).toBeVisible();
});

test("TraceDetailModal — 닫기 버튼으로 닫힌다", async ({ page }) => {
  await page.goto("/logging");
  await page.getByTestId("traces-row-evt-10").click();
  await expect(page.getByTestId("trace-detail-modal")).toBeVisible();
  await page.getByTestId("trace-detail-close").click();
  await expect(page.getByTestId("trace-detail-modal")).toBeHidden();
});
