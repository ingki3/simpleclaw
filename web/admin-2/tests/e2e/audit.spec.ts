/**
 * Audit e2e (BIZ-123) — 헤더 + 4-variant + 6개 필터 + Undo Confirm 흐름.
 */
import { expect, test } from "@playwright/test";

test("Audit — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/audit");

  await expect(
    page.getByRole("heading", { level: 1, name: "감사" }),
  ).toBeVisible();

  await expect(page.getByTestId("audit-list")).toBeVisible();
  await expect(page.getByTestId("audit-rows")).toBeVisible();
  await expect(page.getByTestId("audit-search")).toBeVisible();
  await expect(page.getByTestId("audit-area")).toBeVisible();
  await expect(page.getByTestId("audit-action")).toBeVisible();
  await expect(page.getByTestId("audit-actor")).toBeVisible();
  await expect(page.getByTestId("audit-range")).toBeVisible();
  await expect(page.getByTestId("audit-failed-only")).toBeVisible();
});

test.describe("AuditList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?audit=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/audit?audit=${state}`);
      const list = page.getByTestId("audit-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant — aria-busy='true' + 스켈레톤", async ({ page }) => {
    await page.goto("/audit?audit=loading");
    const list = page.getByTestId("audit-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("audit-list-loading")).toBeVisible();
  });

  test("error variant — alert role", async ({ page }) => {
    await page.goto("/audit?audit=error");
    const err = page.getByTestId("audit-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test("영역 필터 — secrets 만 남긴다", async ({ page }) => {
  await page.goto("/audit");
  await page.getByTestId("audit-area").selectOption("secrets");
  await expect(page.getByTestId("audit-row-audit-2")).toBeVisible();
  await expect(page.getByTestId("audit-row-audit-1")).toBeHidden();
});

test("실패만 보기 토글 — failed entry 만 노출", async ({ page }) => {
  await page.goto("/audit");
  await page.getByTestId("audit-failed-only").click();
  await expect(page.getByTestId("audit-row-audit-10")).toBeVisible();
  await expect(page.getByTestId("audit-row-audit-1")).toBeHidden();
});

test("applied 행 Undo 클릭 → UndoConfirmModal 오픈", async ({ page }) => {
  await page.goto("/audit");
  await page.getByTestId("audit-row-audit-1-undo").click();
  await expect(page.getByTestId("undo-confirm-modal")).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 2, name: "변경 되돌리기" }),
  ).toBeVisible();
  await expect(page.getByTestId("undo-confirm-target")).toContainText(
    "llm.providers.claude/timeout_ms",
  );
  await expect(page.getByTestId("undo-confirm-before")).toContainText("30000");
  await expect(page.getByTestId("undo-confirm-after")).toContainText("60000");
});

test("UndoConfirmModal — 취소 버튼으로 닫힌다", async ({ page }) => {
  await page.goto("/audit");
  await page.getByTestId("audit-row-audit-1-undo").click();
  await expect(page.getByTestId("undo-confirm-modal")).toBeVisible();
  await page.getByTestId("undo-confirm-cancel").click();
  await expect(page.getByTestId("undo-confirm-modal")).toBeHidden();
});

test("UndoConfirmModal — 되돌리기 버튼으로 닫힌다 (mutation 박제)", async ({
  page,
}) => {
  await page.goto("/audit");
  await page.getByTestId("audit-row-audit-1-undo").click();
  await page.getByTestId("undo-confirm-submit").click();
  await expect(page.getByTestId("undo-confirm-modal")).toBeHidden();
});
