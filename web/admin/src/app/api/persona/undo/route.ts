/**
 * POST /api/persona/undo — 5분 윈도 내 undo 토큰으로 직전 상태를 복원.
 *
 * body: `{ token: string }`. 만료되었거나 알 수 없는 토큰이면 410.
 */

import { NextResponse, type NextRequest } from "next/server";
import { applyUndo } from "@/lib/api/persona-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: { token?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (typeof body.token !== "string") {
    return NextResponse.json({ error: "token_required" }, { status: 400 });
  }
  const result = await applyUndo(body.token);
  if (!result) {
    return NextResponse.json({ error: "expired_or_unknown" }, { status: 410 });
  }
  return NextResponse.json({ ok: true, ...result });
}
