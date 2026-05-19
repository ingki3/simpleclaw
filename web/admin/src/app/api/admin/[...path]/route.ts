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

import {
  markDaemonUnreachable,
  markTokenChecked,
  markTokenMismatch,
  markTokenMissing,
} from "@/lib/adminTokenStatus";

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
    // BIZ-245 — 토큰 미설정 상태도 부팅 훅과 같은 채널로 기록해 운영자가 콘솔/배너에서
    // 즉시 감지하도록 한다.
    markTokenMissing();
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
    const cause = err instanceof Error ? err.message : String(err);
    // BIZ-245 — 데몬 도달 실패는 토큰 미스매치와 분리해서 추적한다(운영자 액션이 다름).
    markDaemonUnreachable(`${method} ${upstream} 실패: ${cause}`);
    return NextResponse.json(
      {
        error:
          "데몬에 연결할 수 없어요. 데몬이 실행 중인지, ADMIN_API_BASE가 올바른지 확인해 주세요.",
        cause,
      },
      { status: 502 },
    );
  }

  // BIZ-245 — 401 은 ``.env.local`` 의 ``ADMIN_API_TOKEN`` 이 데몬과 어긋났을 때
  // 가장 흔하게 발생한다 (BIZ-244 사고 원인). 부팅 시점에는 헬스 체크가 200 이었어도
  // 운영 중 회전이 일어나면 여기서 처음 잡힌다.
  if (upstreamResp.status === 401) {
    markTokenMismatch(`${method} ${upstream} → 401`);
  } else if (upstreamResp.ok) {
    markTokenChecked(`${method} ${upstream} → ${upstreamResp.status}`);
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
