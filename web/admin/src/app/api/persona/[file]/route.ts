/**
 * /api/persona/[file] — 페르소나 파일 단건 GET / PUT(?dry_run=true) / DELETE.
 *
 * `[file]` 슬러그는 `soul | agent | user | memory` 중 하나. 그 외는 400.
 * PUT body는 `{ content: string }`. `?dry_run=true`이면 디스크에 쓰지 않고
 * 토큰 합산만 반환한다(어셈블 경고 사전 점검 용).
 * DELETE는 영구 삭제 — 화면 흐름상 MEMORY.md에만 노출되며 ConfirmGate 통과를 강제한다.
 */

import { NextResponse, type NextRequest } from "next/server";
import {
  PERSONA_TYPES,
  type PersonaFileType,
  readPersona,
  writePersona,
  deletePersonaFile,
} from "@/lib/api/persona-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ file: string }>;
}

function parseType(file: string): PersonaFileType | null {
  const lc = file.toLowerCase();
  return (PERSONA_TYPES as readonly string[]).includes(lc)
    ? (lc as PersonaFileType)
    : null;
}

export async function GET(_req: NextRequest, ctx: Params) {
  const { file } = await ctx.params;
  const type = parseType(file);
  if (!type) return NextResponse.json({ error: "unknown_file" }, { status: 400 });
  const meta = await readPersona(type);
  return NextResponse.json(meta);
}

export async function PUT(req: NextRequest, ctx: Params) {
  const { file } = await ctx.params;
  const type = parseType(file);
  if (!type) return NextResponse.json({ error: "unknown_file" }, { status: 400 });

  const url = new URL(req.url);
  const dryRun = url.searchParams.get("dry_run") === "true";

  let body: { content?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (typeof body.content !== "string") {
    return NextResponse.json({ error: "content_required" }, { status: 400 });
  }

  const result = await writePersona(type, body.content, { dryRun });
  return NextResponse.json({
    ok: true,
    ...result,
    hotReloadable: true,
  });
}

export async function DELETE(_req: NextRequest, ctx: Params) {
  const { file } = await ctx.params;
  const type = parseType(file);
  if (!type) return NextResponse.json({ error: "unknown_file" }, { status: 400 });
  const result = await deletePersonaFile(type);
  return NextResponse.json(result);
}
