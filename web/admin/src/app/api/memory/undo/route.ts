/**
 * POST /api/memory/undo — 직전 항목 변경(편집/삭제)을 되돌린다.
 *
 * Body: `{ token: string }`. 5분 윈도 내 최초 1회만 유효.
 */

import { NextResponse, type NextRequest } from "next/server";
import { applyUndo, parseMemoryIndex } from "@/lib/api/memory-server";

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
    return NextResponse.json({ error: "undo_expired" }, { status: 410 });
  }
  return NextResponse.json({
    ok: true,
    entries: parseMemoryIndex(result.content),
  });
}
