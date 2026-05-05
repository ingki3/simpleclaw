/**
 * LLM Router e2e (BIZ-115) — 카드 그리드 + 4-variant + 모달 트리거.
 */
import { expect, test } from "@playwright/test";

test("LLM Router — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/llm-router");

  await expect(
    page.getByRole("heading", { level: 1, name: "LLM 라우터" }),
  ).toBeVisible();

  await expect(page.getByTestId("providers-grid")).toBeVisible();
  await expect(page.getByTestId("fallback-chain")).toBeVisible();
  await expect(page.getByTestId("routing-rules")).toBeVisible();

  // 기본 fixture — claude / openai / gemini 3개 카드.
  for (const id of ["claude", "openai", "gemini"]) {
    await expect(page.getByTestId(`provider-card-${id}`)).toBeVisible();
  }
});

test.describe("ProvidersGrid 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?providers=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/llm-router?providers=${state}`);
      const grid = page.getByTestId("providers-grid");
      await expect(grid).toBeVisible();
      await expect(grid).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant 은 aria-busy='true' 와 스켈레톤", async ({ page }) => {
    await page.goto("/llm-router?providers=loading");
    const grid = page.getByTestId("providers-grid");
    await expect(grid).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("providers-grid-loading")).toBeVisible();
  });

  test("empty variant 은 EmptyState + Add CTA", async ({ page }) => {
    await page.goto("/llm-router?providers=empty");
    await expect(page.getByTestId("providers-grid-empty")).toBeVisible();
  });

  test("error variant 은 alert role", async ({ page }) => {
    await page.goto("/llm-router?providers=error");
    const err = page.getByTestId("providers-grid-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
  });
});

test("'+ 프로바이더 추가' 클릭 → AddProviderModal 노출", async ({ page }) => {
  await page.goto("/llm-router");
  await page.getByTestId("providers-grid-add").click();
  await expect(page.getByTestId("add-provider-modal")).toBeVisible();
  await page.getByTestId("add-provider-cancel").click();
  await expect(page.getByTestId("add-provider-modal")).toBeHidden();
});

test("provider 카드 '편집' → EditProviderModal prefill", async ({ page }) => {
  await page.goto("/llm-router");
  await page.getByTestId("provider-card-claude-edit").click();
  await expect(page.getByTestId("edit-provider-modal")).toBeVisible();
  await expect(page.getByTestId("edit-provider-keyring")).toContainText(
    "claude_api_key",
  );
  await page.getByTestId("edit-provider-cancel").click();
  await expect(page.getByTestId("edit-provider-modal")).toBeHidden();
});

test("규칙 '편집' → RoutingRuleEditorModal + dry-run", async ({ page }) => {
  await page.goto("/llm-router");
  await page.getByTestId("routing-rule-rule-code-edit").click();
  await expect(page.getByTestId("routing-rule-modal")).toBeVisible();
  await expect(page.getByTestId("rule-provider-order")).toBeVisible();
  await page.getByTestId("routing-rule-dryrun").click();
  // dry-run 은 콘솔로만 박제 — modal 은 닫히지 않는다 (mock).
  await expect(page.getByTestId("routing-rule-modal")).toBeVisible();
  await page.getByTestId("routing-rule-cancel").click();
});
