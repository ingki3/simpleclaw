/**
 * Admin API 프록시 라우트 — Next 서버 측에서 토큰을 주입한다.
 *
 * 브라우저는 ``/api/admin/...``로만 요청하고, 본 핸들러가 ``ADMIN_API_BASE``로
 * forward하면서 ``Authorization: Bearer <ADMIN_API_TOKEN>``을 붙인다.
 * 토큰은 절대 클라이언트 번들에 포함되지 않는다(프로세스 환경변수만 참조).
 *
 * 환경 변수:
 *   - ``ADMIN_API_TOKEN``  (필수) — keyring/`.env.local`에 보관된 토큰.
 *   - ``ADMIN_API_BASE``   (선택, 기본 ``http://127.0.0.1:8082``) — 데몬 host:port.
 *
 * 본 라우트는 ``/api/admin/foo/bar`` → ``${ADMIN_API_BASE}/admin/v1/foo/bar``로 매핑한다.
 * 데몬은 항상 ``/admin/v1`` prefix 위에 핸들러를 마운트하므로 클라이언트 경로는
 * v1 prefix를 적지 않는다 — ``fetchAdmin('/config/llm')``이면 충분하다.
 */

import { NextRequest, NextResponse } from "next/server";

const DEFAULT_BASE = "http://127.0.0.1:8082";

function resolveBase(): string {
  const base = process.env.ADMIN_API_BASE ?? DEFAULT_BASE;
  return base.replace(/\/$/, "");
}

async function proxy(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await ctx.params;
  const token = process.env.ADMIN_API_TOKEN;
  if (!token) {
    return NextResponse.json(
      {
        error:
          "ADMIN_API_TOKEN이 설정되지 않았어요. .env.local 또는 시스템 환경변수에 토큰을 넣어 주세요.",
      },
      { status: 503 },
    );
  }

  const base = resolveBase();
  const search = request.nextUrl.search;
  const upstream = `${base}/admin/v1/${path.map(encodeURIComponent).join("/")}${search}`;

  // 헤더 화이트리스트 — 인증과 멱등키만 forward, 호스트/쿠키는 떨어낸다.
  const forwardHeaders = new Headers();
  forwardHeaders.set("Authorization", `Bearer ${token}`);
  const passthrough = ["content-type", "idempotency-key", "x-trace-id"];
  for (const h of passthrough) {
    const v = request.headers.get(h);
    if (v) forwardHeaders.set(h, v);
  }

  // GET/HEAD는 body가 없어야 한다 — RequestInit 규칙.
  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";

  let upstreamResp: Response;
  try {
    upstreamResp = await fetch(upstream, {
      method,
      headers: forwardHeaders,
      body: hasBody ? await request.text() : undefined,
      // Next 서버 측 fetch 캐시 비활성화 — admin은 항상 실시간이어야 한다.
      cache: "no-store",
    });
  } catch (err) {
    return NextResponse.json(
      {
        error:
          "데몬에 연결할 수 없어요. 데몬이 실행 중인지, ADMIN_API_BASE가 올바른지 확인해 주세요.",
        cause: err instanceof Error ? err.message : String(err),
      },
      { status: 502 },
    );
  }

  // 응답 그대로 forward — content-type 보존.
  const respHeaders = new Headers();
  const ct = upstreamResp.headers.get("content-type");
  if (ct) respHeaders.set("content-type", ct);
  return new NextResponse(upstreamResp.body, {
    status: upstreamResp.status,
    headers: respHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
