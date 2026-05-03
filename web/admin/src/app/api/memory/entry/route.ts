/**
 * /api/memory/entry — MEMORY.md 항목 1건 PATCH(편집) / DELETE(영구 삭제).
 *
 * PATCH body: `{ id: string, text: string }` — id는 `${section}:${line}`.
 *   - text는 마커와 type 토큰 없이 본문만. 그대로 한 줄로 기록된다.
 *
 * DELETE body: `{ id: string, confirmation: 'MEMORY.md' }` — Confirm 텍스트가 일치해야만 삭제.
 *   - 일치하지 않으면 400. 진행 시 5분 undo 토큰 반환.
 */

import { NextResponse, type NextRequest } from "next/server";
import {
  parseMemoryIndex,
  readMemoryFile,
  removeEntry,
  replaceEntry,
  writeMemoryFileWithUndo,
} from "@/lib/api/memory-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(req: NextRequest) {
  let body: { id?: unknown; text?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (typeof body.id !== "string" || typeof body.text !== "string") {
    return NextResponse.json({ error: "id_text_required" }, { status: 400 });
  }
  const file = await readMemoryFile();
  if (!file.exists) {
    return NextResponse.json({ error: "memory_file_missing" }, { status: 404 });
  }
  const next = replaceEntry(file.content, body.id, body.text);
  if (next == null) {
    return NextResponse.json({ error: "entry_not_found" }, { status: 404 });
  }
  const { undoToken } = await writeMemoryFileWithUndo(next, file.content);
  const entries = parseMemoryIndex(next);
  return NextResponse.json({ ok: true, undoToken, entries });
}

export async function DELETE(req: NextRequest) {
  let body: { id?: unknown; confirmation?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (typeof body.id !== "string") {
    return NextResponse.json({ error: "id_required" }, { status: 400 });
  }
  if (body.confirmation !== "MEMORY.md") {
    return NextResponse.json(
      { error: "confirmation_mismatch" },
      { status: 400 },
    );
  }
  const file = await readMemoryFile();
  if (!file.exists) {
    return NextResponse.json({ error: "memory_file_missing" }, { status: 404 });
  }
  const next = removeEntry(file.content, body.id);
  if (next == null) {
    return NextResponse.json({ error: "entry_not_found" }, { status: 404 });
  }
  const { undoToken } = await writeMemoryFileWithUndo(next, file.content);
  const entries = parseMemoryIndex(next);
  return NextResponse.json({ ok: true, undoToken, entries });
}
