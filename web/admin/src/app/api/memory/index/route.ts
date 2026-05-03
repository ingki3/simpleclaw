/**
 * GET /api/memory/index — Memory 화면 초기 로드용 단일 엔드포인트.
 *
 * 응답 본문은 통계, MEMORY.md 본문, 파싱된 항목 리스트, 드리밍 상태를 한 번에 담는다.
 * 화면이 화면 전환 시 한 호출로 필요한 정보 대부분을 받기 위해 묶었다.
 */

import { NextResponse } from "next/server";
import {
  getDreamingState,
  parseMemoryIndex,
  readMemoryFile,
  readStats,
} from "@/lib/api/memory-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const file = await readMemoryFile();
  const stats = readStats();
  const entries = parseMemoryIndex(file.content);
  return NextResponse.json({
    stats,
    file: {
      exists: file.exists,
      updatedAt: file.updatedAt,
      sizeBytes: Buffer.byteLength(file.content, "utf8"),
      content: file.content,
    },
    entries,
    dreaming: getDreamingState(),
  });
}
