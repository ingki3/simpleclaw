/**
 * fetchAdmin 단위 테스트 — MSW로 데몬 응답을 모킹.
 *
 * 검증 포인트:
 *  - 2xx → JSON 파싱.
 *  - 401/403/422/500 → AdminApiError로 정규화 + kind 분류.
 *  - 네트워크 실패 → kind="network".
 *  - POST/PATCH는 Idempotency-Key가 자동 부여.
 *  - bearerToken 옵션이 Authorization 헤더에 들어간다.
 *  - onAdminApiError 리스너로 토스트 라우팅 가능.
 */

import { afterAll, afterEach, beforeAll, describe, expect, test, vi } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import {
  fetchAdmin,
  setAdminApiBaseUrl,
  onAdminApiError,
  AdminApiError,
} from "@/lib/api";

const BASE = "http://test.local/admin/v1";

const server = setupServer();

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAdminApiBaseUrl(BASE);
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

describe("fetchAdmin", () => {
  test("GET 2xx — 본문 JSON 파싱", async () => {
    server.use(
      http.get(`${BASE}/health`, () =>
        HttpResponse.json({ status: "ok", uptime_seconds: 42 }),
      ),
    );
    const result = await fetchAdmin<{ status: string; uptime_seconds: number }>(
      "/health",
    );
    expect(result.status).toBe("ok");
    expect(result.uptime_seconds).toBe(42);
  });

  test("PATCH는 Idempotency-Key를 자동 부여", async () => {
    let captured: string | null = null;
    server.use(
      http.patch(`${BASE}/config/llm`, ({ request }) => {
        captured = request.headers.get("Idempotency-Key");
        return HttpResponse.json({ outcome: "applied" });
      }),
    );
    await fetchAdmin("/config/llm", { method: "PATCH", json: { foo: 1 } });
    expect(captured).toBeTruthy();
    expect(captured).toMatch(/-/); // UUID 형태.
  });

  test("외부에서 명시한 idempotencyKey가 보존된다", async () => {
    let captured: string | null = null;
    server.use(
      http.post(`${BASE}/audit/abc/undo`, ({ request }) => {
        captured = request.headers.get("Idempotency-Key");
        return HttpResponse.json({ outcome: "applied", audit_id: "x" });
      }),
    );
    await fetchAdmin("/audit/abc/undo", {
      method: "POST",
      idempotencyKey: "fixed-key-123",
    });
    expect(captured).toBe("fixed-key-123");
  });

  test("bearerToken 옵션이 Authorization 헤더로 들어간다", async () => {
    let auth: string | null = null;
    server.use(
      http.get(`${BASE}/health`, ({ request }) => {
        auth = request.headers.get("Authorization");
        return HttpResponse.json({ status: "ok" });
      }),
    );
    await fetchAdmin("/health", { bearerToken: "tok-abc" });
    expect(auth).toBe("Bearer tok-abc");
  });

  test("401 → AdminApiError(kind=unauthorized)", async () => {
    server.use(
      http.get(`${BASE}/health`, () =>
        HttpResponse.json({ error: "Unauthorized" }, { status: 401 }),
      ),
    );
    await expect(fetchAdmin("/health")).rejects.toMatchObject({
      name: "AdminApiError",
      status: 401,
      kind: "unauthorized",
    });
  });

  test("403 → kind=forbidden", async () => {
    server.use(
      http.get(`${BASE}/health`, () =>
        HttpResponse.json({ error: "forbidden" }, { status: 403 }),
      ),
    );
    await expect(fetchAdmin("/health")).rejects.toMatchObject({
      kind: "forbidden",
    });
  });

  test("422 응답의 errors 배열이 details에 보존된다", async () => {
    server.use(
      http.patch(`${BASE}/config/llm`, () =>
        HttpResponse.json(
          {
            error: "Validation failed",
            errors: ["llm.router.primary는 필수입니다", "model은 문자열이어야 함"],
          },
          { status: 422 },
        ),
      ),
    );
    let caught: AdminApiError | null = null;
    try {
      await fetchAdmin("/config/llm", { method: "PATCH", json: {} });
    } catch (e) {
      caught = e as AdminApiError;
    }
    expect(caught).toBeInstanceOf(AdminApiError);
    expect(caught?.kind).toBe("validation");
    expect(caught?.details.errors).toHaveLength(2);
    expect(caught?.details.errors?.[0]).toContain("필수");
  });

  test("5xx → kind=server", async () => {
    server.use(
      http.get(`${BASE}/health`, () =>
        HttpResponse.json({ error: "boom" }, { status: 503 }),
      ),
    );
    await expect(fetchAdmin("/health")).rejects.toMatchObject({
      kind: "server",
      status: 503,
    });
  });

  test("네트워크 실패 → kind=network, status=0", async () => {
    server.use(http.get(`${BASE}/health`, () => HttpResponse.error()));
    await expect(fetchAdmin("/health")).rejects.toMatchObject({
      kind: "network",
      status: 0,
    });
  });

  test("onAdminApiError 리스너에 에러가 전달된다", async () => {
    const listener = vi.fn();
    const off = onAdminApiError(listener);
    server.use(
      http.get(`${BASE}/health`, () =>
        HttpResponse.json({ error: "no" }, { status: 401 }),
      ),
    );
    await expect(fetchAdmin("/health")).rejects.toBeInstanceOf(AdminApiError);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener.mock.calls[0]?.[0]?.kind).toBe("unauthorized");
    off();
  });

  test("json + body 동시 지정 시 즉시 에러", async () => {
    await expect(
      fetchAdmin("/health", {
        method: "POST",
        json: { a: 1 },
        body: "raw",
      }),
    ).rejects.toThrow(/json.*body/i);
  });
});
