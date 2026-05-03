/**
 * /api/memory/dreaming — 드리밍 상태 조회(GET) / 트리거(POST).
 *
 * 진행 중에 다시 POST하면 409로 응답해 클라이언트가 트리거 버튼을 disable로 두도록 한다.
 */

import { NextResponse } from "next/server";
import {
  getDreamingState,
  triggerDreaming,
} from "@/lib/api/memory-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(getDreamingState());
}

export async function POST() {
  const result = triggerDreaming();
  if (!result.ok) {
    return NextResponse.json(
      { error: "already_running", state: result.state },
      { status: 409 },
    );
  }
  return NextResponse.json({ ok: true, state: result.state });
}
