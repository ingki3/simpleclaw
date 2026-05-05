/**
 * Dashboard e2e (BIZ-114) — 5개 섹션 시각 회귀 + ActiveProjectsPanel 4-variant.
 */
import { expect, test } from "@playwright/test";

test("Dashboard — 5개 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/dashboard");

  await expect(
    page.getByRole("heading", { level: 1, name: "대시보드" }),
  ).toBeVisible();

  // 1) 4도메인 헬스 띠
  await expect(page.getByTestId("system-status-row")).toBeVisible();
  for (const key of ["daemon", "llm", "webhook", "cron"]) {
    await expect(page.getByTestId(`system-status-${key}`)).toBeVisible();
  }

  // 2) 4-카드 metric 그리드
  await expect(page.getByTestId("dashboard-metrics")).toBeVisible();
  for (const key of ["messages24h", "tokens24h", "alerts", "uptime"]) {
    await expect(page.getByTestId(`metric-${key}`)).toBeVisible();
  }

  // 3) Active Projects 패널 — default
  const panel = page.getByTestId("active-projects-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute("data-state", "default");
  await expect(page.getByTestId("active-projects-list")).toBeVisible();

  // 4) 최근 변경 / 5) 최근 에러
  await expect(page.getByTestId("recent-activity")).toBeVisible();
  await expect(page.getByTestId("recent-alerts")).toBeVisible();
});

test("system status 칩 클릭 시 영역 라우트로 이동", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByTestId("system-status-cron").click();
  await expect(page).toHaveURL(/\/cron$/);
});

test.describe("ActiveProjectsPanel 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?activeProjects=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/dashboard?activeProjects=${state}`);
      const panel = page.getByTestId("active-projects-panel");
      await expect(panel).toBeVisible();
      await expect(panel).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant 은 aria-busy='true' 와 스켈레톤 노출", async ({ page }) => {
    await page.goto("/dashboard?activeProjects=loading");
    const panel = page.getByTestId("active-projects-panel");
    await expect(panel).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("active-projects-loading")).toBeVisible();
  });

  test("empty variant 은 EmptyState + 기억 영역 링크", async ({ page }) => {
    await page.goto("/dashboard?activeProjects=empty");
    await expect(page.getByTestId("active-projects-empty")).toBeVisible();
    await expect(page.getByText(/비어 있습니다/)).toBeVisible();
  });

  test("error variant 은 alert role 과 메시지", async ({ page }) => {
    await page.goto("/dashboard?activeProjects=error");
    const err = page.getByTestId("active-projects-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test("recent-activity '전체 보기' 가 /audit 으로 이동", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByTestId("recent-activity-view-all").click();
  await expect(page).toHaveURL(/\/audit$/);
});

test("recent-alerts '전체 보기' 가 /logging 으로 이동", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByTestId("recent-alerts-view-all").click();
  await expect(page).toHaveURL(/\/logging$/);
});
