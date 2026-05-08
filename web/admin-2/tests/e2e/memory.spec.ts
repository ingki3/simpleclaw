/**
 * Memory e2e (BIZ-119) — 헤더 + 4-variant + Insights/Reject/Source/Dry-run.
 */
import { expect, test } from "@playwright/test";

test("Memory — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/memory");

  await expect(
    page.getByRole("heading", { level: 1, name: "기억" }),
  ).toBeVisible();

  await expect(page.getByTestId("section-active-projects")).toBeVisible();
  await expect(page.getByTestId("section-clusters")).toBeVisible();
  await expect(page.getByTestId("section-insights")).toBeVisible();
  await expect(page.getByTestId("section-blocklist")).toBeVisible();
  await expect(page.getByTestId("memory-search")).toBeVisible();
  await expect(page.getByTestId("memory-dry-run")).toBeVisible();
  await expect(page.getByTestId("memory-insights-cards")).toBeVisible();
  await expect(page.getByTestId("active-projects-panel")).toBeVisible();
});

test.describe("InsightsList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?insights=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/memory?insights=${state}`);
      const list = page.getByTestId("memory-insights-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant — aria-busy='true'", async ({ page }) => {
    await page.goto("/memory?insights=loading");
    const list = page.getByTestId("memory-insights-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("memory-insights-list-loading")).toBeVisible();
  });

  test("error variant — alert role", async ({ page }) => {
    await page.goto("/memory?insights=error");
    const err = page.getByTestId("memory-insights-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test.describe("ActiveProjectsPanel 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?projects=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/memory?projects=${state}`);
      const panel = page.getByTestId("active-projects-panel");
      await expect(panel).toBeVisible();
      await expect(panel).toHaveAttribute("data-state", state);
    });
  }
});

test("Dry-run Preview 버튼 → 모달 오픈, 변경 카드 노출, 닫기 동작", async ({
  page,
}) => {
  await page.goto("/memory");
  await page.getByTestId("memory-dry-run").click();
  await expect(page.getByTestId("dry-run-modal")).toBeVisible();
  await expect(page.getByTestId("dry-run-changes")).toBeVisible();
  await page.getByTestId("dry-run-cancel").click();
  await expect(page.getByTestId("dry-run-modal")).toBeHidden();
});

test("출처 보기 버튼 → SourceDrawer 노출", async ({ page }) => {
  await page.goto("/memory");
  await page.getByTestId("memory-insight-ins-001-source").click();
  await expect(page.getByTestId("source-drawer")).toBeVisible();
  await expect(page.getByTestId("source-drawer-meta")).toBeVisible();
  await expect(page.getByTestId("source-drawer-messages")).toBeVisible();
  await page.getByTestId("source-drawer-close").click();
  await expect(page.getByTestId("source-drawer")).toBeHidden();
});

test("거절 → RejectConfirmModal → 확정 시 blocklist 추가 + 큐에서 제거", async ({
  page,
}) => {
  await page.goto("/memory");
  await page.getByTestId("memory-insight-ins-001-reject").click();
  await expect(page.getByTestId("reject-confirm-modal")).toBeVisible();
  await page.getByTestId("reject-confirm-submit").click();
  await expect(page.getByTestId("reject-confirm-modal")).toBeHidden();
  await expect(page.getByTestId("blocklist-morning-briefing")).toBeVisible();
  await expect(page.getByTestId("memory-insight-ins-001")).toBeHidden();
});

test("채택 버튼 클릭 → 큐에서 즉시 제거", async ({ page }) => {
  await page.goto("/memory");
  await expect(page.getByTestId("memory-insight-ins-002")).toBeVisible();
  await page.getByTestId("memory-insight-ins-002-accept").click();
  await expect(page.getByTestId("memory-insight-ins-002")).toBeHidden();
});

test("검색 — 토픽 키워드로 인사이트 필터링", async ({ page }) => {
  await page.goto("/memory");
  await page.getByTestId("memory-search").fill("stocks");
  await expect(page.getByTestId("memory-insight-ins-002")).toBeVisible();
  await expect(page.getByTestId("memory-insight-ins-001")).toBeHidden();
});

test("Blocklist 차단 해제 → 표에서 행 제거", async ({ page }) => {
  await page.goto("/memory");
  await expect(page.getByTestId("blocklist-joke.daily")).toBeVisible();
  await page.getByTestId("blocklist-joke.daily-unblock").click();
  await expect(page.getByTestId("blocklist-joke.daily")).toBeHidden();
});
