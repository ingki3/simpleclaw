/**
 * insights / blocklist API 래퍼 단위 테스트 — BIZ-92.
 *
 * 검증:
 *  - ``listInsights()`` 가 status 인자를 query string 으로 통과시키는지.
 *  - ``listBlocklist()`` 응답 envelope 통과.
 */

import { afterAll, afterEach, beforeAll, describe, expect, test } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { setAdminApiBaseUrl } from "@/lib/api";
import { listBlocklist, listInsights } from "@/lib/api/insights";

const BASE = "http://test.local/admin/v1";
const server = setupServer();

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAdminApiBaseUrl(BASE);
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("listInsights", () => {
  test("status 미지정 시 query 없이 호출하고 envelope 그대로 노출", async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.get(`${BASE}/memory/insights`, ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({
          insights: [],
          total: 0,
          active_count: 3,
          archived_count: 1,
        });
      }),
    );
    const res = await listInsights();
    expect(capturedUrl).toMatch(/\/memory\/insights$/);
    expect(res.active_count).toBe(3);
    expect(res.archived_count).toBe(1);
  });

  test("status=archived 를 query 로 보낸다", async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.get(`${BASE}/memory/insights`, ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({
          insights: [
            {
              topic: "x",
              text: "y",
              evidence_count: 1,
              confidence: 0.5,
              first_seen: "2026-01-01T00:00:00Z",
              last_seen: "2026-01-02T00:00:00Z",
              start_msg_id: null,
              end_msg_id: null,
              source_msg_ids: [],
              archived_at: "2026-01-03T00:00:00Z",
            },
          ],
          total: 1,
          active_count: 0,
          archived_count: 1,
        });
      }),
    );
    const res = await listInsights("archived");
    expect(capturedUrl).toMatch(/status=archived/);
    expect(res.insights[0].archived_at).toBe("2026-01-03T00:00:00Z");
  });
});

describe("listBlocklist", () => {
  test("entries 와 total 을 그대로 노출한다", async () => {
    server.use(
      http.get(`${BASE}/memory/blocklist`, () =>
        HttpResponse.json({
          entries: [
            {
              topic: "Cron noise",
              topic_key: "cron_noise",
              reason: "auto",
              blocked_at: "2026-04-01T10:00:00Z",
            },
          ],
          total: 1,
        }),
      ),
    );
    const res = await listBlocklist();
    expect(res.total).toBe(1);
    expect(res.entries[0].topic_key).toBe("cron_noise");
  });
});
