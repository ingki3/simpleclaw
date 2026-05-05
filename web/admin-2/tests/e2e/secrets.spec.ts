/**
 * Secrets e2e (BIZ-120) — 헤더 + 4-variant + Add + Rotate ConfirmGate + 평문 누출 금지.
 *
 * DoD — 시크릿 값이 콘솔/네트워크에 노출되지 않는지 명시적으로 검증한다.
 */
import { expect, test } from "@playwright/test";

test("Secrets — 헤더와 핵심 섹션이 모두 보인다", async ({ page }) => {
  await page.goto("/secrets");

  await expect(
    page.getByRole("heading", { level: 1, name: "시크릿" }),
  ).toBeVisible();
  await expect(page.getByTestId("secrets-search")).toBeVisible();
  await expect(page.getByTestId("secrets-add")).toBeVisible();
  await expect(page.getByTestId("secrets-counts")).toBeVisible();
  await expect(page.getByTestId("secrets-list")).toBeVisible();
  // 기본 fixture 의 첫 행.
  await expect(
    page.getByTestId("secret-row-keyring:llm.anthropic_api_key"),
  ).toBeVisible();
});

test.describe("SecretsList 4-variant", () => {
  for (const state of ["default", "loading", "empty", "error"] as const) {
    test(`?secrets=${state} → data-state="${state}"`, async ({ page }) => {
      await page.goto(`/secrets?secrets=${state}`);
      const list = page.getByTestId("secrets-list");
      await expect(list).toBeVisible();
      await expect(list).toHaveAttribute("data-state", state);
    });
  }

  test("loading variant — aria-busy='true' + 스켈레톤", async ({ page }) => {
    await page.goto("/secrets?secrets=loading");
    const list = page.getByTestId("secrets-list");
    await expect(list).toHaveAttribute("aria-busy", "true");
    await expect(page.getByTestId("secrets-list-loading")).toBeVisible();
  });

  test("error variant — alert role + 재시도 버튼", async ({ page }) => {
    await page.goto("/secrets?secrets=error");
    const err = page.getByTestId("secrets-list-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveAttribute("role", "alert");
    await expect(page.getByTestId("secrets-list-retry")).toBeVisible();
  });
});

test("'＋ 시크릿 추가' 버튼 → AddSecretModal 오픈", async ({ page }) => {
  await page.goto("/secrets");
  await page.getByTestId("secrets-add").click();
  await expect(page.getByTestId("add-secret-modal")).toBeVisible();
  await page.getByTestId("add-secret-cancel").click();
  await expect(page.getByTestId("add-secret-modal")).toBeHidden();
});

test("Add Secret — 정상 입력 시 새 행이 마스킹된 상태로 추가된다", async ({
  page,
}) => {
  await page.goto("/secrets");
  await page.getByTestId("secrets-add").click();

  await page.getByTestId("add-secret-key-name").fill("service.e2e_token");
  await page.getByTestId("add-secret-value").fill("xxxx-PLAINTEXT-9999");
  await page.getByTestId("add-secret-submit").click();

  await expect(page.getByTestId("add-secret-modal")).toBeHidden();
  const newRow = page.getByTestId("secret-row-keyring:service.e2e_token");
  await expect(newRow).toBeVisible();
  await expect(newRow).toContainText("••••9999");
  // 평문이 어디에도 등장하지 않는다.
  await expect(page.locator("body")).not.toContainText("PLAINTEXT");
});

test("Add Secret — 검증 실패 (빈 값) 시 저장 차단", async ({ page }) => {
  await page.goto("/secrets");
  await page.getByTestId("secrets-add").click();
  await page.getByTestId("add-secret-submit").click();
  await expect(page.getByTestId("add-secret-key-name-error")).toBeVisible();
  await expect(page.getByTestId("add-secret-value-error")).toBeVisible();
  // 모달은 그대로 열려 있다.
  await expect(page.getByTestId("add-secret-modal")).toBeVisible();
});

test("회전 클릭 → RotateConfirmModal (alertdialog) + ConfirmGate", async ({
  page,
}) => {
  await page.goto("/secrets");
  const row = page.getByTestId("secret-row-keyring:llm.anthropic_api_key");
  await row.getByRole("button", { name: "회전" }).click();
  const modal = page.getByTestId("rotate-confirm-modal");
  await expect(modal).toBeVisible();
  await expect(modal).toHaveAttribute("role", "alertdialog");
  await expect(page.getByTestId("rotate-confirm-target")).toContainText(
    "llm.anthropic_api_key",
  );
  await expect(page.getByTestId("rotate-confirm-gate")).toBeVisible();

  // 잘못된 키워드 입력 — 실행 버튼 비활성화 유지.
  await page.getByLabel("confirm keyword").fill("rotate");
  await expect(
    page.getByRole("button", { name: "회전 실행" }),
  ).toBeDisabled();

  // ESC 로 닫기.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("rotate-confirm-modal")).toBeHidden();
});

test("검색 — 키 이름 substring 으로 필터링", async ({ page }) => {
  await page.goto("/secrets");
  await page.getByTestId("secrets-search").fill("anthropic");
  await expect(
    page.getByTestId("secret-row-keyring:llm.anthropic_api_key"),
  ).toBeVisible();
  await expect(
    page.getByTestId("secret-row-keyring:llm.openai_api_key"),
  ).toBeHidden();
  // 검색 활성 시 그룹 헤더 없이 flat.
  await expect(page.getByTestId("secrets-flat")).toBeVisible();
  await expect(page.getByTestId("secrets-grouped")).toBeHidden();
});

test("DoD — 시크릿 값이 콘솔/네트워크에 노출되지 않는다", async ({ page }) => {
  const consoleMessages: string[] = [];
  page.on("console", (msg) => {
    consoleMessages.push(`${msg.type()}::${msg.text()}`);
  });
  // POST/PUT 본문에서 평문이 새는지 보존.
  const requestBodies: string[] = [];
  page.on("request", (req) => {
    const body = req.postData();
    if (body) requestBodies.push(body);
  });

  const PLAINTEXT = "ZZZ-PLAINTEXT-do-not-leak-1234";

  await page.goto("/secrets");
  await page.getByTestId("secrets-add").click();
  await page.getByTestId("add-secret-key-name").fill("service.leak_check");
  await page.getByTestId("add-secret-value").fill(PLAINTEXT);
  await page.getByTestId("add-secret-submit").click();
  await expect(page.getByTestId("add-secret-modal")).toBeHidden();

  // 평문은 콘솔에 절대 등장하지 않는다 — *길이* 박제만 허용.
  for (const m of consoleMessages) {
    expect(m).not.toContain(PLAINTEXT);
    expect(m).not.toContain("PLAINTEXT");
  }
  // 평문은 어떤 네트워크 요청 본문에도 포함되지 않는다.
  for (const b of requestBodies) {
    expect(b).not.toContain(PLAINTEXT);
    expect(b).not.toContain("PLAINTEXT");
  }
  // DOM 에도 평문이 등장하지 않는다 — 마지막 4자리만.
  await expect(page.locator("body")).not.toContainText("PLAINTEXT");
  await expect(
    page.getByTestId("secret-row-keyring:service.leak_check"),
  ).toContainText("••••1234");
});
