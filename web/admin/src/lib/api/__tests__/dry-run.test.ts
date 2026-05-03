/**
 * dryRun 헬퍼 — BIZ-41 ``?dry_run=true`` 응답 구조와 정합성을 검증.
 *
 * 백엔드 admin_api._handle_patch_config_area의 dry_run 분기 응답 모양은:
 *   {
 *     "outcome": "dry_run",
 *     "diff": { "before": {...}, "after": {...} },
 *     "policy": { "level": "...", "requires_restart": bool, "affected_modules": [...] }
 *   }
 *
 * 본 테스트는 (a) 정상 응답을 그대로 ``DryRunResult``로 매핑하는지, (b) outcome이
 * dry_run이 아니면 명시적 에러를 던지는지를 확인한다.
 */

import { afterAll, afterEach, beforeAll, describe, expect, test } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { dryRun, setAdminApiBaseUrl, AdminApiError } from "@/lib/api";

const BASE = "http://test.local/admin/v1";
const server = setupServer();

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAdminApiBaseUrl(BASE);
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("dryRun", () => {
  test("정상 dry-run 응답을 매핑한다", async () => {
    server.use(
      http.patch(`${BASE}/config/webhook`, ({ request }) => {
        const url = new URL(request.url);
        // 호출자가 dry_run 쿼리를 명시적으로 붙였는지 검증.
        if (url.searchParams.get("dry_run") !== "true") {
          return HttpResponse.json({ error: "dry_run 미요청" }, { status: 400 });
        }
        return HttpResponse.json({
          outcome: "dry_run",
          diff: {
            before: { rate_limit: 60 },
            after: { rate_limit: 30 },
          },
          policy: {
            level: "hot",
            requires_restart: false,
            affected_modules: ["webhook"],
            hot: ["webhook.rate_limit"],
          },
        });
      }),
    );

    const result = await dryRun("webhook", { rate_limit: 30 });
    expect(result.diff.before).toEqual({ rate_limit: 60 });
    expect(result.diff.after).toEqual({ rate_limit: 30 });
    expect(result.policy.level).toBe("hot");
    expect(result.policy.affected_modules).toContain("webhook");
  });

  test("outcome이 dry_run이 아니면 에러", async () => {
    server.use(
      http.patch(`${BASE}/config/webhook`, () =>
        HttpResponse.json({
          outcome: "applied",
          audit_id: "x",
          policy: { level: "hot", requires_restart: false, affected_modules: [] },
        }),
      ),
    );
    await expect(dryRun("webhook", { rate_limit: 30 })).rejects.toBeInstanceOf(
      AdminApiError,
    );
  });

  test("422 응답이면 검증 에러로 위임된다", async () => {
    server.use(
      http.patch(`${BASE}/config/webhook`, () =>
        HttpResponse.json(
          { error: "Validation failed", errors: ["rate_limit must be > 0"] },
          { status: 422 },
        ),
      ),
    );
    await expect(dryRun("webhook", { rate_limit: -1 })).rejects.toMatchObject({
      kind: "validation",
      details: { errors: expect.arrayContaining(["rate_limit must be > 0"]) },
    });
  });
});
