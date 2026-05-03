/**
 * Secrets API 래퍼 단위 테스트 — BIZ-50.
 *
 * 검증 포인트:
 *  - ``listSecrets``  : ``{secrets: [...]}`` 응답을 받아 (backend, name) 사전순 정렬.
 *  - ``revealSecret`` : POST 본문은 ``{}``, ``backend`` 인자는 query string에 들어간다.
 *  - ``rotateSecret`` : POST 본문은 ``{value, backend}``, name은 path-encoded.
 *  - ``findSecretReferences`` :
 *      · ``keyring:foo`` 같은 참조 문법을 dotted-path로 정확히 잡는다.
 *      · 다른 시크릿 이름은 무시한다.
 *      · 영역(area)은 path의 *최상위* 키로 정한다.
 *      · 트리에 시크릿 참조가 없으면 빈 배열을 돌려준다.
 *
 * MSW로 데몬 응답을 가로챈다 — client.test.ts와 동일 패턴.
 */

import { afterAll, afterEach, beforeAll, describe, expect, test } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { setAdminApiBaseUrl } from "@/lib/api";
import {
  findSecretReferences,
  listSecrets,
  revealSecret,
  rotateSecret,
} from "@/lib/api/secrets";

const BASE = "http://test.local/admin/v1";
const server = setupServer();

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAdminApiBaseUrl(BASE);
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("listSecrets", () => {
  test("응답을 (backend, name) 사전순으로 정렬한다", async () => {
    server.use(
      http.get(`${BASE}/secrets`, () =>
        HttpResponse.json({
          secrets: [
            { name: "openai_api_key", backend: "keyring", last_rotated_at: null },
            { name: "claude_api_key", backend: "keyring", last_rotated_at: null },
            { name: "telegram_token", backend: "env", last_rotated_at: null },
          ],
        }),
      ),
    );
    const items = await listSecrets();
    // env가 keyring보다 사전순으로 먼저 — env/telegram_token이 첫 자리.
    expect(items.map((s) => `${s.backend}:${s.name}`)).toEqual([
      "env:telegram_token",
      "keyring:claude_api_key",
      "keyring:openai_api_key",
    ]);
  });

  test("응답에 secrets 키가 없으면 빈 배열", async () => {
    server.use(http.get(`${BASE}/secrets`, () => HttpResponse.json({})));
    const items = await listSecrets();
    expect(items).toEqual([]);
  });
});

describe("revealSecret", () => {
  test("backend 인자는 query string으로, 본문은 빈 객체", async () => {
    let capturedQuery: string | null = null;
    let capturedBody: unknown = null;
    server.use(
      http.post(`${BASE}/secrets/claude_api_key/reveal`, async ({ request }) => {
        capturedQuery = new URL(request.url).searchParams.get("backend");
        capturedBody = await request.json();
        return HttpResponse.json({
          name: "claude_api_key",
          backend: "keyring",
          value: "sk-abcd1234",
          nonce: "n1",
          expires_in_seconds: 15,
        });
      }),
    );
    const res = await revealSecret("claude_api_key", "keyring");
    expect(capturedQuery).toBe("keyring");
    expect(capturedBody).toEqual({});
    expect(res.value).toBe("sk-abcd1234");
  });

  test("backend 미지정 시 query 없음", async () => {
    let capturedUrl: string | null = null;
    server.use(
      http.post(`${BASE}/secrets/foo/reveal`, ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({
          name: "foo",
          backend: "keyring",
          value: "x",
          nonce: "n",
          expires_in_seconds: 15,
        });
      }),
    );
    await revealSecret("foo");
    expect(capturedUrl).toMatch(/\/secrets\/foo\/reveal$/);
  });
});

describe("rotateSecret", () => {
  test("본문은 {value, backend}", async () => {
    let captured: unknown = null;
    server.use(
      http.post(`${BASE}/secrets/claude_api_key/rotate`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({
          outcome: "applied",
          audit_id: "a1",
          backend: "keyring",
          name: "claude_api_key",
        });
      }),
    );
    const res = await rotateSecret("claude_api_key", "sk-new", "keyring");
    expect(captured).toEqual({ value: "sk-new", backend: "keyring" });
    expect(res.outcome).toBe("applied");
    expect(res.audit_id).toBe("a1");
  });

  test("이름이 path-encoded 된다", async () => {
    server.use(
      http.post(`${BASE}/secrets/foo%20bar/rotate`, () =>
        HttpResponse.json({
          outcome: "applied",
          audit_id: "a2",
          backend: "keyring",
          name: "foo bar",
        }),
      ),
    );
    // 공백이 들어간 이름이 정확히 ``foo%20bar`` 경로로 인코딩되는지 — 핸들러가
    // 일치하지 않으면 MSW가 unhandled request로 던지므로 통과 자체가 검증.
    const res = await rotateSecret("foo bar", "v");
    expect(res.audit_id).toBe("a2");
  });
});

describe("findSecretReferences", () => {
  const config = {
    llm: {
      providers: {
        claude: { api_key: "keyring:claude_api_key" },
        openai: { api_key: "env:openai_api_key" },
      },
    },
    webhook: {
      hmac_secret: "keyring:claude_api_key",
      rate_limit: 60,
    },
    telegram: {
      bot_token: "keyring:telegram_token",
    },
    misc: {
      // 시크릿 참조 문법이 아닌 일반 문자열 — 잡히면 안 됨.
      label: "claude_api_key",
      number: 42,
      flag: true,
    },
  };

  test("``backend:name``과 일치하는 모든 경로를 찾는다", () => {
    const refs = findSecretReferences(config, "claude_api_key");
    const paths = refs.map((r) => r.path).sort();
    expect(paths).toEqual([
      "llm.providers.claude.api_key",
      "webhook.hmac_secret",
    ]);
  });

  test("backend는 참조 문법에서 추출", () => {
    const refs = findSecretReferences(config, "openai_api_key");
    expect(refs).toHaveLength(1);
    expect(refs[0]).toMatchObject({
      path: "llm.providers.openai.api_key",
      area: "llm",
      backend: "env",
    });
  });

  test("area는 dotted-path의 최상위 키", () => {
    const refs = findSecretReferences(config, "telegram_token");
    expect(refs).toHaveLength(1);
    expect(refs[0].area).toBe("telegram");
  });

  test("참조 문법이 아닌 동일 문자열은 무시", () => {
    // misc.label은 "claude_api_key" 자체이므로 reference가 아님.
    const refs = findSecretReferences(config, "claude_api_key");
    expect(refs.find((r) => r.path === "misc.label")).toBeUndefined();
  });

  test("일치하는 참조가 없으면 빈 배열", () => {
    expect(findSecretReferences(config, "no_such_secret")).toEqual([]);
  });

  test("배열도 재귀 탐색", () => {
    const tree = {
      channels: {
        webhooks: [
          { token: "env:hook1" },
          { token: "keyring:hook2" },
        ],
      },
    };
    const refs1 = findSecretReferences(tree, "hook1");
    expect(refs1).toHaveLength(1);
    expect(refs1[0].path).toBe("channels.webhooks.0.token");
    const refs2 = findSecretReferences(tree, "hook2");
    expect(refs2[0].backend).toBe("keyring");
  });

  test("null/undefined 트리에 안전", () => {
    expect(findSecretReferences(null, "x")).toEqual([]);
    expect(findSecretReferences(undefined, "x")).toEqual([]);
    expect(findSecretReferences({}, "x")).toEqual([]);
  });
});
