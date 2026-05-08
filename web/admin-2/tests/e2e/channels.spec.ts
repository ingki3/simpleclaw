/**
 * Channels e2e (BIZ-121) — 헤더, Telegram/Webhook 카드, 4-variant, 모달 3종.
 */
import { expect, test } from "@playwright/test";

test("Channels — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/channels");

  await expect(
    page.getByRole("heading", { level: 1, name: "채널" }),
  ).toBeVisible();
  await expect(page.getByTestId("telegram-card")).toBeVisible();
  await expect(page.getByTestId("webhook-list")).toBeVisible();

  // fixture — github / multica endpoint 카드.
  await expect(page.getByTestId("webhook-endpoint-github")).toBeVisible();
  await expect(page.getByTestId("webhook-endpoint-multica")).toBeVisible();
});

test.describe("WebhookList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?webhooks=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/channels?webhooks=${state}`);
      const list = page.getByTestId("webhook-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant 은 aria-busy='true' + 스켈레톤", async ({ page }) => {
    await page.goto("/channels?webhooks=loading");
    const list = page.getByTestId("webhook-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("webhook-list-loading")).toBeVisible();
  });

  test("error variant 은 alert role + 재시도 버튼", async ({ page }) => {
    await page.goto("/channels?webhooks=error");
    const err = page.getByTestId("webhook-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
    await expect(page.getByTestId("webhook-list-retry")).toBeVisible();
  });
});

test("Telegram 회전 → TokenRotateModal 흐름", async ({ page }) => {
  await page.goto("/channels");
  await page.getByTestId("telegram-bot-token-rotate").click();
  await expect(page.getByTestId("token-rotate-modal")).toBeVisible();
  // 키워드 입력 전 confirm 은 disabled.
  await expect(page.getByTestId("token-rotate-confirm")).toBeDisabled();
  await page.getByTestId("token-rotate-keyword").fill("ROTATE");
  // 카운트다운 3초 — 4초 대기 후 활성화.
  await page.waitForTimeout(3500);
  await expect(page.getByTestId("token-rotate-confirm")).toBeEnabled();
  // 취소도 동작.
  await page.getByTestId("token-rotate-cancel").click();
  await expect(page.getByTestId("token-rotate-modal")).toBeHidden();
});

test("Webhook endpoint 편집 → WebhookEditModal 열기 + prefill", async ({
  page,
}) => {
  await page.goto("/channels");
  await page.getByTestId("webhook-endpoint-github-edit").click();
  await expect(page.getByTestId("webhook-edit-modal")).toBeVisible();
  await expect(page.getByTestId("webhook-edit-modal")).toContainText("github");
  await expect(page.getByTestId("webhook-edit-url")).toHaveValue(
    "https://hooks.simpleclaw.dev/github",
  );
  await page.getByTestId("webhook-edit-cancel").click();
  await expect(page.getByTestId("webhook-edit-modal")).toBeHidden();
});

test("Webhook 편집 → 트래픽 시뮬레이션 모달", async ({ page }) => {
  await page.goto("/channels");
  await page.getByTestId("webhook-endpoint-github-edit").click();
  await page.getByTestId("webhook-edit-simulate").click();
  await expect(page.getByTestId("traffic-simulation-modal")).toBeVisible();
  // 메트릭 3종 가시.
  await expect(page.getByTestId("traffic-simulation-served")).toBeVisible();
  await expect(page.getByTestId("traffic-simulation-queued")).toBeVisible();
  await expect(page.getByTestId("traffic-simulation-rejected")).toBeVisible();
  await page.getByTestId("traffic-simulation-close").click();
  await expect(page.getByTestId("traffic-simulation-modal")).toBeHidden();
});

test("Webhook URL 비우면 검증 실패", async ({ page }) => {
  await page.goto("/channels");
  await page.getByTestId("webhook-endpoint-github-edit").click();
  await page.getByTestId("webhook-edit-url").fill("");
  await page.getByTestId("webhook-edit-submit").click();
  await expect(page.getByTestId("webhook-edit-url-error")).toBeVisible();
});

test("endpoint Switch 토글이 즉시 반영된다", async ({ page }) => {
  await page.goto("/channels");
  const toggle = page.getByTestId("webhook-endpoint-legacy-slack-toggle");
  // legacy-slack 은 fixture 에서 disabled 로 시작 — 클릭 시 enabled 로 전환.
  await expect(toggle).toBeVisible();
  await toggle.click();
  // aria-checked 확인 — Switch 의 토글 결과.
  await expect(toggle).toHaveAttribute("aria-checked", "true");
});
