/**
 * GET /api/persona/resolve — 4개 파일을 토큰 예산 내에서 어셈블한 결과(dry-run).
 *
 * Resolver 미리보기 패널이 호출한다. 우선순위는 SOUL → AGENT → USER → MEMORY,
 * 예산 초과 시 MEMORY → USER 순으로 끝에서 절삭한다(어셈블러 정책 모사).
 */

import { NextResponse } from "next/server";
import { assemblePrompt } from "@/lib/api/persona-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await assemblePrompt();
  return NextResponse.json(data);
}
