/**
 * useUndo / registerUndo — 5분 윈도우 슬롯 동작 검증.
 *
 * 모듈 레벨 store이므로 각 테스트마다 ``consumeUndo()``로 초기화한다.
 */

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import {
  registerUndo,
  consumeUndo,
  getUndoSlot,
  setAdminApiBaseUrl,
  fetchAdmin,
} from "@/lib/api";

const BASE = "http://test.local/admin/v1";
const server = setupServer();

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAdminApiBaseUrl(BASE);
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  consumeUndo();
});

describe("undo store", () => {
  test("registerUndo은 슬롯을 만들고, getUndoSlot으로 조회 가능", () => {
    const slot = registerUndo("audit-1", "LLM 변경");
    expect(slot.auditId).toBe("audit-1");
    expect(slot.label).toBe("LLM 변경");
    expect(slot.expiresAt).toBeGreaterThan(slot.registeredAt);
    expect(getUndoSlot()?.auditId).toBe("audit-1");
  });

  test("새 register는 이전 슬롯을 덮어쓴다", () => {
    registerUndo("a", "A");
    registerUndo("b", "B");
    expect(getUndoSlot()?.auditId).toBe("b");
  });

  test("consumeUndo는 슬롯을 비운다", () => {
    registerUndo("x", "X");
    consumeUndo();
    expect(getUndoSlot()).toBeNull();
  });

  test("백엔드 undo 호출 — POST /audit/{id}/undo로 라우팅된다", async () => {
    let captured: string | null = null;
    server.use(
      http.post(`${BASE}/audit/aid-99/undo`, ({ request }) => {
        captured = request.headers.get("Idempotency-Key");
        return HttpResponse.json({ outcome: "applied", audit_id: "new-1" });
      }),
    );
    const result = await fetchAdmin<{ outcome: string; audit_id: string }>(
      "/audit/aid-99/undo",
      { method: "POST" },
    );
    expect(result.outcome).toBe("applied");
    expect(captured).toBeTruthy();
  });
});
