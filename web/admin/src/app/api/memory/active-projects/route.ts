/**
 * GET /api/memory/active-projects — `/memory` 상단 Active Projects 패널의 데이터.
 *
 * `.agent/active_projects.jsonl` (BIZ-74) sidecar 를 직접 읽어 패널 spec 모델로 변환한다
 * (BIZ-96). Python admin API 가 들어오면 본 라우트는 그쪽으로 프록시하도록 교체된다 —
 * 다른 `/api/memory/*` 라우트와 동일한 정책.
 */

import { NextResponse } from "next/server";
import { readActiveProjects } from "@/lib/api/memory-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await readActiveProjects();
  return NextResponse.json(data);
}
