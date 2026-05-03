/**
 * GET /api/persona — 4개 페르소나 파일의 메타+내용+토큰 카운트를 반환.
 *
 * 본 Route Handler는 Python `/admin/v1/persona`가 신설될 때까지의 임시 어댑터로,
 * 같은 리포의 `.agent/{SOUL,AGENT,USER,MEMORY}.md`를 직접 읽는다.
 */

import { NextResponse } from "next/server";
import { listPersonaFiles } from "@/lib/api/persona-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await listPersonaFiles();
  return NextResponse.json(data);
}
