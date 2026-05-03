/**
 * GET /api/memory/export?from=ISO&to=ISO — 대화 메시지를 JSONL로 다운로드.
 *
 * Content-Type은 `application/x-ndjson`로 설정. 빈 결과여도 200 + 빈 본문.
 * 브라우저에서 anchor `download` 속성으로 파일 저장 시 사용한다.
 */

import { NextResponse, type NextRequest } from "next/server";
import { exportConversationsJsonl } from "@/lib/api/memory-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  const body = exportConversationsJsonl(from, to);
  const safeRange = `${from ?? "all"}_${to ?? "now"}`.replace(/[^A-Za-z0-9_.-]/g, "");
  return new NextResponse(body, {
    status: 200,
    headers: {
      "content-type": "application/x-ndjson; charset=utf-8",
      "content-disposition": `attachment; filename="simpleclaw-conversations-${safeRange}.jsonl"`,
      "cache-control": "no-store",
    },
  });
}
