/**
 * Memory Route Handler 공용 서버 모듈 — 디스크 IO + MEMORY.md 인덱스 파싱 + undo 버퍼.
 *
 * Python `/admin/v1/memory/*` 엔드포인트 부재 상태에서 Admin UI Memory 화면(BIZ-49)이
 * 동작 가능하도록 같은 리포의 `.agent/MEMORY.md` 와 `.agent/conversations.db` 를
 * 직접 다룬다. 후속 이슈에서 Python admin API가 들어오면 본 모듈은 해당 백엔드를
 * 프록시하도록 교체된다 — Persona 스캐폴딩(persona-server.ts)과 동일한 정책.
 *
 * MEMORY.md 인덱스 모델:
 *  - `## YYYY-MM-DD` 헤더가 *섹션*을 나누고, 그 아래 `- `로 시작하는 라인이 *항목*이다.
 *  - 항목 `id`는 (section_index, line_index) 조합. 파일 재파싱 시에도 같은 위치면 동일.
 *  - 항목 본문 시작에 `[user]`, `[feedback]`, `[project]`, `[reference]` 토큰이 있으면
 *    type을 그렇게 분류한다(대소문자 구분 없음). 없으면 `null`.
 *
 * 드리밍 트리거는 본 모듈의 인메모리 상태를 토글한다 — 실제 드리밍 파이프라인은 데몬에서
 * 별도 트리거 파일/시그널로 동작하므로, 본 모듈은 5단계 progress 시뮬레이션만 제공한다.
 * Python admin API가 들어오면 실제 상태로 바뀐다.
 *
 * undo 버퍼는 Persona와 동일한 5분 윈도 in-memory map.
 */

import { promises as fs, existsSync, statSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";

const MEMORY_FILENAME = "MEMORY.md";
const CONVERSATIONS_DB = "conversations.db";

/** 5분 윈도 단발 undo. */
const UNDO_WINDOW_MS = 5 * 60 * 1000;

export type MemoryEntryType = "user" | "feedback" | "project" | "reference";

export interface MemoryEntry {
  /** 안정 id — `${sectionIndex}:${lineIndex}` 형식. */
  id: string;
  /** 0-base 섹션 번호. */
  sectionIndex: number;
  /** 섹션 라벨(예: "2026-04-28") — `## ` 다음 텍스트. */
  section: string;
  /** 0-base 항목 라인 번호(섹션 내). */
  lineIndex: number;
  /** 본문(`-` 토큰을 떼고 trim). */
  text: string;
  /** 분류 토큰 — 본문 선두에 `[user]` 등이 붙어 있으면 추출. */
  type: MemoryEntryType | null;
}

export interface MemoryStats {
  /** `.agent/conversations.db` 파일 크기(byte) — 없으면 0. */
  diskBytes: number;
  /** 대화 메시지 총 수 — `sqlite3` CLI 사용 가능 시 실측, 아니면 `null`. */
  totalMessages: number | null;
  /** 마지막 드리밍 시각 ISO — MEMORY.md mtime. 파일 부재 시 null. */
  lastDreamingAt: string | null;
}

export interface DreamingState {
  /** 현재 드리밍 진행 중 여부 — true면 트리거 disable. */
  running: boolean;
  /** 0..4 — DESIGN.md §4.9 5단계 stepper와 1:1. running이 false면 null. */
  step: number | null;
  /** 사람이 읽는 라벨. */
  stepLabel: string | null;
  /** 시작 시각 ISO. */
  startedAt: string | null;
  /** 직전 회차 종료 시각 ISO — null이면 한 번도 안 돈 것. */
  lastFinishedAt: string | null;
  /** 직전 회차 결과 — success/failure. */
  lastOutcome: "success" | "failure" | null;
  /** 직전 회차 메시지 — 실패 사유 또는 요약. */
  lastMessage: string | null;
}

interface UndoEntry {
  previousContent: string;
  expiresAt: number;
}

const undoBuffer = new Map<string, UndoEntry>();

function gcUndo(): void {
  const now = Date.now();
  for (const [k, v] of undoBuffer.entries()) {
    if (v.expiresAt <= now) undoBuffer.delete(k);
  }
}

export function agentDir(): string {
  const env = process.env.SIMPLECLAW_AGENT_DIR;
  if (env) return path.resolve(env);
  // web/admin → 리포 루트 → .agent
  return path.join(/*turbopackIgnore: true*/ process.cwd(), "..", "..", ".agent");
}

function memoryFile(): string {
  return path.join(agentDir(), MEMORY_FILENAME);
}

function conversationsDbFile(): string {
  return path.join(agentDir(), CONVERSATIONS_DB);
}

// --------------------------------------------------------------------
// MEMORY.md 파싱
// --------------------------------------------------------------------

/**
 * MEMORY.md 텍스트를 섹션·항목 리스트로 파싱한다.
 *
 * 규약:
 *  - `## ` 또는 `# ` 헤더 라인이 섹션 경계.
 *  - `- ` 또는 `* ` 로 시작하는 라인이 항목.
 *  - 항목 본문은 마커를 떼고 `trim()`.
 *  - 본문 선두 `[user|feedback|project|reference]` 토큰을 type으로 추출(대소문자 무시).
 */
export function parseMemoryIndex(text: string): MemoryEntry[] {
  const lines = text.split(/\r?\n/);
  const entries: MemoryEntry[] = [];
  // 섹션 0은 헤더 이전 자유 텍스트(루트). 섹션 카운트는 ## 또는 # 헤더 등장 시 증가.
  let currentSection = "(root)";
  let sectionIndex = -1;
  let lineWithinSection = 0;

  // 시작 시 가상 root 섹션 생성 — header 없이 작성된 항목도 카운트.
  sectionIndex = 0;

  for (const raw of lines) {
    const line = raw.trim();
    const headerMatch = /^#{1,3}\s+(.+)$/.exec(line);
    if (headerMatch) {
      sectionIndex += 1;
      currentSection = headerMatch[1].trim();
      lineWithinSection = 0;
      continue;
    }
    const bulletMatch = /^[-*]\s+(.+)$/.exec(line);
    if (!bulletMatch) continue;
    const body = bulletMatch[1].trim();
    let typed: MemoryEntryType | null = null;
    let displayText = body;
    const typeMatch = /^\[(user|feedback|project|reference)\]\s*(.*)$/i.exec(body);
    if (typeMatch) {
      typed = typeMatch[1].toLowerCase() as MemoryEntryType;
      displayText = typeMatch[2].trim();
    }
    entries.push({
      id: `${sectionIndex}:${lineWithinSection}`,
      sectionIndex,
      section: currentSection,
      lineIndex: lineWithinSection,
      text: displayText,
      type: typed,
    });
    lineWithinSection += 1;
  }

  return entries;
}

/**
 * 항목 1건을 새 본문으로 교체한 MEMORY.md 본문을 반환한다. 매칭되지 않으면 null.
 *
 * 라인을 추적할 때 파싱과 동일한 규약(헤더 카운트, 섹션 내 라인 카운트)을 사용한다.
 * 새 본문에 `[type]` 토큰을 다시 prefix하지 않는다 — 본문 그대로 적용한다.
 */
export function replaceEntry(
  text: string,
  id: string,
  newBody: string,
): string | null {
  const target = parseId(id);
  if (!target) return null;
  const lines = text.split(/\r?\n/);
  const out: string[] = [];
  let sectionIndex = 0;
  let lineWithinSection = 0;
  let replaced = false;

  for (const raw of lines) {
    const trimmed = raw.trim();
    const isHeader = /^#{1,3}\s+/.test(trimmed);
    const isBullet = /^[-*]\s+/.test(trimmed);

    if (isHeader) {
      sectionIndex += 1;
      lineWithinSection = 0;
      out.push(raw);
      continue;
    }

    if (
      isBullet &&
      sectionIndex === target.sectionIndex &&
      lineWithinSection === target.lineIndex
    ) {
      // 들여쓰기 + 마커는 보존하고 본문만 교체.
      const indent = raw.match(/^(\s*)/)?.[1] ?? "";
      const marker = trimmed.startsWith("*") ? "*" : "-";
      out.push(`${indent}${marker} ${newBody}`);
      lineWithinSection += 1;
      replaced = true;
      continue;
    }

    if (isBullet) {
      lineWithinSection += 1;
    }
    out.push(raw);
  }

  return replaced ? out.join("\n") : null;
}

/**
 * 항목 1건을 제거한 MEMORY.md 본문을 반환한다. 매칭되지 않으면 null.
 *
 * 제거 시 동일 라인을 단순히 삭제하고, 직후 빈 라인이 연속되면 1개로 압축한다.
 */
export function removeEntry(text: string, id: string): string | null {
  const target = parseId(id);
  if (!target) return null;
  const lines = text.split(/\r?\n/);
  const out: string[] = [];
  let sectionIndex = 0;
  let lineWithinSection = 0;
  let removed = false;

  for (const raw of lines) {
    const trimmed = raw.trim();
    const isHeader = /^#{1,3}\s+/.test(trimmed);
    const isBullet = /^[-*]\s+/.test(trimmed);

    if (isHeader) {
      sectionIndex += 1;
      lineWithinSection = 0;
      out.push(raw);
      continue;
    }

    if (
      !removed &&
      isBullet &&
      sectionIndex === target.sectionIndex &&
      lineWithinSection === target.lineIndex
    ) {
      // 라인 삭제 — 카운터는 증가시키지 않는다(이후 항목 id가 1씩 당겨짐).
      // ``!removed`` 가드로 동일 섹션의 다음 bullet이 같은 id로 재매칭되는 것을 막는다.
      removed = true;
      continue;
    }

    if (isBullet) {
      lineWithinSection += 1;
    }
    out.push(raw);
  }

  if (!removed) return null;
  // 연속 빈 라인 압축 — 단순 가독성용, 의미는 동일.
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}

function parseId(id: string): { sectionIndex: number; lineIndex: number } | null {
  const m = /^(\d+):(\d+)$/.exec(id);
  if (!m) return null;
  return {
    sectionIndex: Number(m[1]),
    lineIndex: Number(m[2]),
  };
}

// --------------------------------------------------------------------
// 디스크 IO
// --------------------------------------------------------------------

export async function readMemoryFile(): Promise<{
  exists: boolean;
  content: string;
  updatedAt: string | null;
}> {
  const fp = memoryFile();
  try {
    const [content, st] = await Promise.all([
      fs.readFile(fp, "utf8"),
      fs.stat(fp),
    ]);
    return {
      exists: true,
      content,
      updatedAt: st.mtime.toISOString(),
    };
  } catch (err) {
    if (isNotFound(err)) return { exists: false, content: "", updatedAt: null };
    throw err;
  }
}

async function writeMemoryFileWithUndo(
  content: string,
  previousContent: string,
): Promise<{ undoToken: string }> {
  const fp = memoryFile();
  await fs.mkdir(path.dirname(fp), { recursive: true });
  await fs.writeFile(fp, content, "utf8");
  gcUndo();
  const token = randomUUID();
  undoBuffer.set(token, {
    previousContent,
    expiresAt: Date.now() + UNDO_WINDOW_MS,
  });
  return { undoToken: token };
}

export async function applyUndo(
  token: string,
): Promise<{ content: string } | null> {
  gcUndo();
  const slot = undoBuffer.get(token);
  if (!slot) return null;
  undoBuffer.delete(token);
  const fp = memoryFile();
  await fs.mkdir(path.dirname(fp), { recursive: true });
  await fs.writeFile(fp, slot.previousContent, "utf8");
  return { content: slot.previousContent };
}

// --------------------------------------------------------------------
// 통계
// --------------------------------------------------------------------

export function readStats(): MemoryStats {
  const dbPath = conversationsDbFile();
  const memPath = memoryFile();

  let diskBytes = 0;
  if (existsSync(dbPath)) {
    try {
      diskBytes = statSync(dbPath).size;
    } catch {
      diskBytes = 0;
    }
  }

  let lastDreamingAt: string | null = null;
  if (existsSync(memPath)) {
    try {
      lastDreamingAt = statSync(memPath).mtime.toISOString();
    } catch {
      lastDreamingAt = null;
    }
  }

  // sqlite3 CLI가 있으면 메시지 수 실측 — 없으면 null.
  let totalMessages: number | null = null;
  if (existsSync(dbPath)) {
    try {
      const proc = spawnSync(
        "sqlite3",
        [dbPath, "SELECT count(*) FROM messages"],
        { encoding: "utf8", timeout: 2000 },
      );
      if (proc.status === 0) {
        const n = parseInt(proc.stdout.trim(), 10);
        if (Number.isFinite(n)) totalMessages = n;
      }
    } catch {
      // 무시 — null로 남는다
    }
  }

  return { diskBytes, totalMessages, lastDreamingAt };
}

// --------------------------------------------------------------------
// 드리밍 상태 (인메모리 시뮬레이션)
// --------------------------------------------------------------------

interface MutableDreamingState extends DreamingState {
  timer: ReturnType<typeof setTimeout> | null;
}

const dreaming: MutableDreamingState = {
  running: false,
  step: null,
  stepLabel: null,
  startedAt: null,
  lastFinishedAt: null,
  lastOutcome: null,
  lastMessage: null,
  timer: null,
};

const STEP_LABELS = [
  "준비 중",
  "메시지 클러스터링",
  "LLM 요약",
  "MEMORY.md 갱신",
  "완료 처리",
];

export function getDreamingState(): DreamingState {
  return {
    running: dreaming.running,
    step: dreaming.step,
    stepLabel: dreaming.stepLabel,
    startedAt: dreaming.startedAt,
    lastFinishedAt: dreaming.lastFinishedAt,
    lastOutcome: dreaming.lastOutcome,
    lastMessage: dreaming.lastMessage,
  };
}

/**
 * 드리밍을 트리거한다. 이미 진행 중이면 `{ ok: false, reason: 'busy' }`.
 *
 * 실제 드리밍 파이프라인 트리거는 데몬 측 책임이라 본 모듈은 5단계 stepper만 시뮬레이션한다 —
 * 단계마다 약 600ms 대기 후 진행. 백엔드 통합 시 실제 진행률을 polling 또는 SSE로 대체.
 */
export function triggerDreaming(): {
  ok: boolean;
  reason?: "busy";
  state: DreamingState;
} {
  if (dreaming.running) {
    return { ok: false, reason: "busy", state: getDreamingState() };
  }
  dreaming.running = true;
  dreaming.step = 0;
  dreaming.stepLabel = STEP_LABELS[0];
  dreaming.startedAt = new Date().toISOString();
  dreaming.lastOutcome = null;
  dreaming.lastMessage = null;

  // 단계별 진행 — 0..4 후 종료.
  const advance = (next: number) => {
    if (next >= STEP_LABELS.length) {
      dreaming.running = false;
      dreaming.step = null;
      dreaming.stepLabel = null;
      dreaming.startedAt = null;
      dreaming.lastFinishedAt = new Date().toISOString();
      dreaming.lastOutcome = "success";
      dreaming.lastMessage = "드리밍이 완료됐어요.";
      dreaming.timer = null;
      return;
    }
    dreaming.step = next;
    dreaming.stepLabel = STEP_LABELS[next];
    dreaming.timer = setTimeout(() => advance(next + 1), 600);
  };
  // 첫 단계는 즉시 다음으로 넘어가지 않고 현재 단계로 노출 — 600ms 후 전이.
  dreaming.timer = setTimeout(() => advance(1), 600);
  return { ok: true, state: getDreamingState() };
}

// --------------------------------------------------------------------
// 대화 내보내기 (JSONL)
// --------------------------------------------------------------------

/**
 * SQLite messages 테이블에서 [from, to] 범위 메시지를 JSONL로 내보낸다.
 *
 * sqlite3 CLI가 없으면 빈 본문 반환. from/to는 ISO timestamp(YYYY-MM-DD 또는 ISO),
 * 미지정 시 전체 범위.
 */
export function exportConversationsJsonl(
  fromISO: string | null,
  toISO: string | null,
): string {
  const dbPath = conversationsDbFile();
  if (!existsSync(dbPath)) return "";

  // SQL 인젝션 방지를 위해 inputs는 escape — 단일 인용부호 두 개로 치환.
  const safe = (s: string) => s.replace(/'/g, "''");
  const conditions: string[] = [];
  if (fromISO) conditions.push(`timestamp >= '${safe(fromISO)}'`);
  if (toISO) conditions.push(`timestamp <= '${safe(toISO)}'`);
  const where = conditions.length ? ` WHERE ${conditions.join(" AND ")}` : "";

  // .mode json + 행 단위 객체 출력. role, content, timestamp 핵심 컬럼만 노출.
  const sql = `
.mode json
SELECT id, role, content, timestamp FROM messages${where} ORDER BY id ASC;
`.trim();

  try {
    const proc = spawnSync("sqlite3", [dbPath], {
      input: sql,
      encoding: "utf8",
      timeout: 30000,
      maxBuffer: 256 * 1024 * 1024,
    });
    if (proc.status !== 0) return "";
    // sqlite3 .mode json은 단일 JSON 배열 — JSONL로 변환.
    const out = proc.stdout.trim();
    if (!out) return "";
    let arr: unknown;
    try {
      arr = JSON.parse(out);
    } catch {
      return "";
    }
    if (!Array.isArray(arr)) return "";
    return arr.map((row) => JSON.stringify(row)).join("\n") + "\n";
  } catch {
    return "";
  }
}

// --------------------------------------------------------------------
// 헬퍼
// --------------------------------------------------------------------

function isNotFound(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: string }).code === "ENOENT"
  );
}

export const ENTRY_TYPES: readonly MemoryEntryType[] = [
  "user",
  "feedback",
  "project",
  "reference",
] as const;

export {
  writeMemoryFileWithUndo,
  // 외부에서 트리거 종료를 강제로 시뮬할 수 있도록 노출(테스트용).
};
