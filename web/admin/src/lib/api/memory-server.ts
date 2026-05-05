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
const ACTIVE_PROJECTS_FILENAME = "active_projects.jsonl";

/**
 * Active Projects 윈도우 (일) — `last_seen >= now - WINDOW_DAYS` 항목만 패널에 노출.
 * Python 측 dreaming 의 ``active_projects.window_days`` (기본 7일, BIZ-74) 와 동일한
 * 정책을 따른다. 수치를 동적으로 읽으려면 config.yaml 파싱이 필요한데, 본 모듈은
 * 데몬 의존을 피하기 위해 정적 기본값으로 고정한다 — 사용자가 다른 윈도우를 쓰면
 * 데몬과 어드민의 표시가 어긋날 수 있으나, sidecar 자체에는 윈도우 외 항목도 그대로
 * 남으므로 데이터 손실은 없다.
 */
const ACTIVE_PROJECTS_WINDOW_DAYS = 7;

/**
 * Gate policy 안내값. 실제 enforcement 는 Python dreaming 파이프라인이 담당하며,
 * 본 모듈은 패널 풋노트 표시용 표상값만 노출한다(BIZ-73 단발 관측 자동 승격 차단,
 * BIZ-74 active-projects 부착 임계).
 */
const GATE_POLICY_DEFAULTS: GatePolicy = {
  single_observation_block: true,
  cluster_threshold: 0.6,
};

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

/**
 * Active Project — `/memory` 상단 패널 한 행에 대응 (BIZ-66/74/96).
 *
 * sidecar(`.agent/active_projects.jsonl`)는 Python 측 ``ActiveProject`` 데이터클래스를
 * 그대로 직렬화한다 (필드: name/role/recent_summary/first_seen/last_seen). 본 응답은
 * 패널 spec 에 맞춰 재가공한 파생 모델이다 — sidecar 의 내부 키와는 별도로 관리.
 *
 * 필드 매핑:
 *   id          ← normalize_name(name)  — 한·영 표기 변형이 있어도 동일 키로 묶임.
 *   title       ← name (사람이 읽는 표기 그대로).
 *   managed     ← 항상 true. sidecar 적재 자체가 dreaming-managed 의 표지(BIZ-66).
 *                  단발 관측은 큐에 머물고 sidecar 에 들어오지 않으므로, 여기 등장한
 *                  시점에서 이미 cluster 채택 임계를 통과했다.
 *   score       ← 윈도우 내 recency 점수 (1.0 = 오늘, 윈도우 끝에서 0.0). 클러스터의
 *                  코사인 점수가 sidecar 에 보존되지 않으므로(BIZ-74 의 의도된 단순화)
 *                  파생 신호로 대체. 사용자가 "최근에 가장 활발한 것"을 빠르게 보도록.
 *   updated_at  ← last_seen (ISO8601).
 */
export interface ActiveProjectSummary {
  id: string;
  title: string;
  managed: boolean;
  score: number;
  updated_at: string;
}

export interface GatePolicy {
  single_observation_block: boolean;
  cluster_threshold: number;
}

export interface ActiveProjectsResponse {
  active_projects: ActiveProjectSummary[];
  gate_policy: GatePolicy;
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
// Active Projects sidecar 리더 (BIZ-96)
// --------------------------------------------------------------------

function activeProjectsFile(): string {
  return path.join(agentDir(), ACTIVE_PROJECTS_FILENAME);
}

/**
 * Python 측 ``normalize_name`` 과 동치 (BIZ-74). 한·영 혼용 표기를 동일 키로 묶기
 * 위해 공백·구두점을 제거하고 영문은 소문자화한다. ``\p{L}\p{N}`` 만 보존하면 한글
 * 음절 (가-힣) 도 유지되며, 한국어→영문 음역은 일부러 하지 않는다(과처리 위험).
 */
function normalizeName(name: string): string {
  if (!name) return "";
  return name
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "");
}

/**
 * sidecar 한 줄을 파싱한다. 손상된 줄은 skip — Python 쪽 ``ActiveProjectStore.load``
 * 와 동일한 관용성. 필수 필드(``name``, ``last_seen``)가 비면 null.
 */
function parseActiveProjectLine(
  line: string,
): { name: string; lastSeen: Date } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const obj = parsed as Record<string, unknown>;
  const name = typeof obj.name === "string" ? obj.name.trim() : "";
  const lastSeenRaw = typeof obj.last_seen === "string" ? obj.last_seen : null;
  if (!name || !lastSeenRaw) return null;
  const ts = new Date(lastSeenRaw);
  if (Number.isNaN(ts.getTime())) return null;
  return { name, lastSeen: ts };
}

/**
 * sidecar(`.agent/active_projects.jsonl`)를 읽어 패널용 모델로 변환한다.
 *
 * 정책:
 *   - 윈도우(``ACTIVE_PROJECTS_WINDOW_DAYS``) 밖 항목은 제외 — sidecar 는 영구 보관이지만
 *     UI 는 활성 항목만 노출(데몬 측 ``filter_active`` 와 동일한 정책).
 *   - 같은 정규형 키가 두 번 등장하면 마지막 줄을 채택 (Python 쪽 ``load`` 와 동치).
 *   - 정렬: ``last_seen`` 내림차순 — "가장 최근" 이 위에.
 *   - 파일 부재 / 빈 파일 / sqlite 등 오류는 모두 빈 리스트로 환원 (UI 에서 empty state).
 *
 * 호출 시점에 ``Date.now()`` 를 기준으로 score 와 윈도우 컷오프를 계산한다 — 테스트는
 * 시간 의존 없이 ``parseActiveProjectLine`` 단위로 검증할 수 있도록 분리해 두었다.
 */
export async function readActiveProjects(): Promise<ActiveProjectsResponse> {
  const fp = activeProjectsFile();
  let raw: string;
  try {
    raw = await fs.readFile(fp, "utf8");
  } catch (err) {
    if (isNotFound(err)) {
      return { active_projects: [], gate_policy: { ...GATE_POLICY_DEFAULTS } };
    }
    throw err;
  }

  const now = Date.now();
  const windowMs = ACTIVE_PROJECTS_WINDOW_DAYS * 24 * 60 * 60 * 1000;
  const cutoff = now - windowMs;

  // 같은 키 중복 시 "마지막 줄 채택" 의미를 살리려고 Map 으로 누적.
  const byKey = new Map<string, { name: string; lastSeen: Date }>();
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parsed = parseActiveProjectLine(trimmed);
    if (!parsed) continue;
    const key = normalizeName(parsed.name);
    if (!key) continue;
    byKey.set(key, parsed);
  }

  const items: ActiveProjectSummary[] = [];
  for (const [key, p] of byKey) {
    const lastSeenMs = p.lastSeen.getTime();
    if (lastSeenMs < cutoff) continue;
    // recency score: 1.0(=now) → 0.0(=윈도우 끝). 음수는 cutoff 가드로 차단됨.
    const score = Math.max(0, Math.min(1, (lastSeenMs - cutoff) / windowMs));
    items.push({
      id: key,
      title: p.name,
      managed: true,
      score: Number(score.toFixed(2)),
      updated_at: p.lastSeen.toISOString(),
    });
  }

  items.sort(
    (a, b) =>
      new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  );

  return {
    active_projects: items,
    gate_policy: { ...GATE_POLICY_DEFAULTS },
  };
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
  parseActiveProjectLine,
  normalizeName,
  ACTIVE_PROJECTS_WINDOW_DAYS,
  GATE_POLICY_DEFAULTS,
  // 외부에서 트리거 종료를 강제로 시뮬할 수 있도록 노출(테스트용).
};
