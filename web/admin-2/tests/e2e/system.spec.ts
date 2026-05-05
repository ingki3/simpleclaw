/**
 * System e2e (BIZ-124) — 8 카드 시각 회귀 + BackupListCard 4-variant +
 * 3 모달 트리거 (재시작 / 백업 상세 / 복원 confirm).
 */
import { expect, test } from "@playwright/test";

test("System — 8 카드와 헤더가 모두 보인다", async ({ page }) => {
  await page.goto("/system");

  await expect(
    page.getByRole("heading", { level: 1, name: "시스템" }),
  ).toBeVisible();

  for (const id of [
    "system-info-card",
    "subsystem-health-card",
    "restart-card",
    "sub-agent-pool-card",
    "security-policy-card",
    "config-snapshot-card",
    "theme-card",
    "backup-list-card",
  ]) {
    await expect(page.getByTestId(id)).toBeVisible();
  }
});

test.describe("BackupListCard 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?backups=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/system?backups=${state}`);
      const card = page.getByTestId("backup-list-card");
      await expect(card).toBeVisible();
      await expect(card).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant 은 aria-busy='true' 와 스켈레톤", async ({ page }) => {
    await page.goto("/system?backups=loading");
    const card = page.getByTestId("backup-list-card");
    await expect(card).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("backup-list-loading")).toBeVisible();
  });

  test("empty variant 은 EmptyState + '지금 백업' CTA", async ({ page }) => {
    await page.goto("/system?backups=empty");
    await expect(page.getByTestId("backup-list-empty")).toBeVisible();
  });

  test("error variant 은 alert role 과 재시도", async ({ page }) => {
    await page.goto("/system?backups=error");
    const err = page.getByTestId("backup-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
    await expect(page.getByTestId("backup-list-retry")).toBeVisible();
  });
});

test("'데몬 재시작' 헤더 → ConfirmRestartDialog 노출 + 두 scope", async ({ page }) => {
  await page.goto("/system");
  await page.getByTestId("system-header-restart").click();
  const dialog = page.getByTestId("confirm-restart-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId("confirm-restart-scope-process")).toBeVisible();
  await expect(page.getByTestId("confirm-restart-scope-service")).toBeVisible();
  // ESC 로 닫기
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("RestartCard 의 '데몬 재시작…' 도 동일 다이얼로그를 연다", async ({
  page,
}) => {
  await page.goto("/system");
  await page.getByTestId("restart-card-trigger").click();
  await expect(page.getByTestId("confirm-restart-dialog")).toBeVisible();
});

test("백업 행 클릭 → BackupDetailModal 이 열리고 '이 백업으로 복원' → RestoreConfirmModal 진입", async ({
  page,
}) => {
  await page.goto("/system");
  // 가장 최근 백업 행을 연다.
  await page.getByTestId("backup-row-backup-2026-05-04-0300-open").click();
  await expect(page.getByTestId("backup-detail-modal")).toBeVisible();
  await expect(page.getByTestId("backup-detail-contents")).toContainText(
    "config(2KB)",
  );

  await page.getByTestId("backup-detail-restore").click();
  await expect(page.getByTestId("backup-detail-modal")).toBeHidden();
  const restore = page.getByTestId("restore-confirm-modal");
  await expect(restore).toBeVisible();
  // 5단계 stepper 노출
  for (const k of ["stop", "snapshot", "restore", "integrity", "start"]) {
    await expect(page.getByTestId(`restore-confirm-step-${k}`)).toBeVisible();
  }
  // dry-run 클릭 시 결과 안내 노출
  await page.getByTestId("restore-confirm-dryrun").click();
  await expect(page.getByTestId("restore-confirm-dryrun-result")).toBeVisible();
});

test("BackupListCard '복원…' → RestoreConfirmModal 직진입", async ({ page }) => {
  await page.goto("/system");
  await page.getByTestId("backup-list-restore").click();
  await expect(page.getByTestId("restore-confirm-modal")).toBeVisible();
});

test("ThemeCard 의 Dark 옵션 클릭 시 <html data-theme='dark'> 가 적용된다", async ({
  page,
}) => {
  await page.goto("/system");
  await page.getByTestId("theme-card-option-dark").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // Light 로 되돌리기 — 후속 테스트에 영향 주지 않게.
  await page.getByTestId("theme-card-option-light").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
