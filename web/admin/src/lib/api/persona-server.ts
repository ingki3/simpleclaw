/**
 * Persona Route Handler 공용 서버 모듈 — 디스크 IO + 토큰 추정 + undo 버퍼.
 *
 * 본 어댑터는 Python `/admin/v1/persona/*` 엔드포인트가 아직 부재한 상태에서
 * Admin UI Persona 화면(BIZ-46)이 동작 가능하도록 같은 리포의 `.agent/*.md`를
 * 직접 읽고 쓴다. 후속 이슈에서 Python admin API가 들어오면 본 모듈은 해당
 * 백엔드를 프록시하도록 교체되어야 한다.
 *
 * 토큰 추정: cl100k_base 정확 카운팅을 위해 별도 라이브러리를 도입하지 않고
 * `chars/4` 휴리스틱(GPT 표준 어림수)을 사용한다. 실제 백엔드(tiktoken)가
 * 들어오면 자연스럽게 정확값으로 대체된다. 응답에는 `tokens` 필드만 노출하므로
 * 프론트는 구현 변경에 영향받지 않는다.
 *
 * undo 버퍼는 프로세스 메모리 내 5분 윈도. Next.js 서버가 재시작되면 휘발한다 —
 * 단일 운영자 도구이고 5분 내 인스턴스 재시작은 운영 시나리오상 드물다는 가정.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";

export type PersonaFileType = "soul" | "agent" | "user" | "memory";

const FILE_MAP: Record<PersonaFileType, string> = {
  soul: "SOUL.md",
  agent: "AGENT.md",
  user: "USER.md",
  memory: "MEMORY.md",
};

const ORDER: PersonaFileType[] = ["soul", "agent", "user", "memory"];

/** 합산 토큰 예산 — config.yaml `persona.token_budget`와 동기되어야 한다. */
const DEFAULT_TOKEN_BUDGET = 8000;

/** 5분 윈도 내 단발 undo. */
const UNDO_WINDOW_MS = 5 * 60 * 1000;

interface UndoEntry {
  type: PersonaFileType;
  previousContent: string;
  previousExisted: boolean;
  expiresAt: number;
}

const undoBuffer = new Map<string, UndoEntry>();

/** 만료된 undo 엔트리를 비워 메모리 누수를 방지한다. */
function gcUndo(): void {
  const now = Date.now();
  for (const [k, v] of undoBuffer.entries()) {
    if (v.expiresAt <= now) undoBuffer.delete(k);
  }
}

/**
 * `.agent` 디렉터리 절대 경로.
 *
 * Next.js 서버는 `web/admin/`에서 기동되므로 두 단계 위가 리포 루트.
 * 환경변수 `SIMPLECLAW_AGENT_DIR`가 있으면 그 값이 우선한다.
 */
export function agentDir(): string {
  const env = process.env.SIMPLECLAW_AGENT_DIR;
  if (env) return path.resolve(env);
  // web/admin/ → 리포 루트 → .agent/
  // turbopackIgnore: process.cwd 기반 동적 경로이므로 NFT 추적에서 제외한다.
  return path.join(/*turbopackIgnore: true*/ process.cwd(), "..", "..", ".agent");
}

function fileFor(type: PersonaFileType): string {
  return path.join(agentDir(), FILE_MAP[type]);
}

/**
 * 휴리스틱 토큰 카운터 — `chars/4` (GPT 계열 표준 추정).
 *
 * 정확한 cl100k_base 카운트는 Python 백엔드가 들어오면 자연 대체된다.
 * 빈 문자열은 0, 그 외에는 최소 1로 보정.
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

export interface DiskFileMeta {
  type: PersonaFileType;
  filename: string;
  exists: boolean;
  content: string;
  tokens: number;
  tokenBudget: number;
  updatedAt: string | null;
}

export async function readPersona(type: PersonaFileType): Promise<DiskFileMeta> {
  const filename = FILE_MAP[type];
  const fp = fileFor(type);
  try {
    const [content, stat] = await Promise.all([
      fs.readFile(fp, "utf8"),
      fs.stat(fp),
    ]);
    return {
      type,
      filename,
      exists: true,
      content,
      tokens: estimateTokens(content),
      tokenBudget: DEFAULT_TOKEN_BUDGET,
      updatedAt: stat.mtime.toISOString(),
    };
  } catch (err) {
    // 파일 부재는 정상 케이스 — 빈 새 파일로 간주
    if (isNotFound(err)) {
      return {
        type,
        filename,
        exists: false,
        content: "",
        tokens: 0,
        tokenBudget: DEFAULT_TOKEN_BUDGET,
        updatedAt: null,
      };
    }
    throw err;
  }
}

export async function listPersonaFiles(): Promise<{
  files: DiskFileMeta[];
  totalTokens: number;
  tokenBudget: number;
}> {
  const files = await Promise.all(ORDER.map(readPersona));
  const totalTokens = files.reduce((sum, f) => sum + f.tokens, 0);
  return { files, totalTokens, tokenBudget: DEFAULT_TOKEN_BUDGET };
}

/**
 * 파일을 디스크에 기록한다. `dryRun`이 true이면 실제로 쓰지 않고 토큰만 계산한다.
 * 실제 쓰기 시 undo 토큰을 반환한다.
 */
export async function writePersona(
  type: PersonaFileType,
  content: string,
  options: { dryRun?: boolean } = {},
): Promise<{
  type: PersonaFileType;
  tokens: number;
  totalTokens: number;
  dryRun: boolean;
  undoToken?: string;
}> {
  const tokens = estimateTokens(content);

  if (options.dryRun) {
    // dry-run: 다른 파일들의 토큰만 합산
    const others = await Promise.all(
      ORDER.filter((t) => t !== type).map(readPersona),
    );
    const totalTokens = tokens + others.reduce((s, f) => s + f.tokens, 0);
    return { type, tokens, totalTokens, dryRun: true };
  }

  // 이전 상태를 undo 버퍼에 보관
  const previous = await readPersona(type);
  const fp = fileFor(type);
  await fs.mkdir(path.dirname(fp), { recursive: true });
  await fs.writeFile(fp, content, "utf8");

  gcUndo();
  const undoToken = randomUUID();
  undoBuffer.set(undoToken, {
    type,
    previousContent: previous.content,
    previousExisted: previous.exists,
    expiresAt: Date.now() + UNDO_WINDOW_MS,
  });

  const all = await listPersonaFiles();
  return {
    type,
    tokens,
    totalTokens: all.totalTokens,
    dryRun: false,
    undoToken,
  };
}

/**
 * MEMORY.md 영구 삭제. 호출자(Route Handler)가 ConfirmGate 통과를 강제해야 한다.
 * 다른 파일 타입에도 동일하게 동작하지만, 화면 흐름상 MEMORY 전용으로 노출된다.
 */
export async function deletePersonaFile(
  type: PersonaFileType,
): Promise<{ ok: true; type: PersonaFileType }> {
  const fp = fileFor(type);
  try {
    await fs.unlink(fp);
  } catch (err) {
    if (!isNotFound(err)) throw err;
  }
  return { ok: true, type };
}

/**
 * undo 버퍼에서 토큰을 찾아 이전 상태로 복원한다. 만료/존재하지 않으면 null.
 */
export async function applyUndo(token: string): Promise<{
  type: PersonaFileType;
  content: string;
  tokens: number;
} | null> {
  gcUndo();
  const entry = undoBuffer.get(token);
  if (!entry) return null;
  undoBuffer.delete(token);

  const fp = fileFor(entry.type);
  if (entry.previousExisted) {
    await fs.mkdir(path.dirname(fp), { recursive: true });
    await fs.writeFile(fp, entry.previousContent, "utf8");
  } else {
    try {
      await fs.unlink(fp);
    } catch (err) {
      if (!isNotFound(err)) throw err;
    }
  }
  return {
    type: entry.type,
    content: entry.previousContent,
    tokens: estimateTokens(entry.previousContent),
  };
}

/**
 * 단순 어셈블 — Python 측 `assemble_prompt`의 텍스트 결합 정책만 모사한다.
 * 우선순위: SOUL → AGENT → USER → MEMORY. 토큰 예산 초과 시 MEMORY → USER 순으로
 * 끝에서 잘라낸다. 백엔드가 들어오면 본 함수는 그 응답을 그대로 반환하도록 교체.
 */
export async function assemblePrompt(): Promise<{
  assembledText: string;
  tokenCount: number;
  tokenBudget: number;
  wasTruncated: boolean;
}> {
  const SEP = "\n\n---\n\n";
  // SOUL을 가장 앞에 두지만 어셈블 정책 자체는 백엔드 결정에 맡긴다 (어셈블러 참조).
  const order: PersonaFileType[] = ["soul", "agent", "user", "memory"];
  const files = await Promise.all(order.map(readPersona));
  const texts = files.map((f) => f.content.trim()).filter(Boolean);
  let assembled = texts.join(SEP);
  let tokens = estimateTokens(assembled);
  let truncated = false;

  // MEMORY → USER 순으로 끝에서 자른다 (어셈블러와 동일한 우선순위)
  const truncOrder: PersonaFileType[] = ["memory", "user"];
  for (const t of truncOrder) {
    if (tokens <= DEFAULT_TOKEN_BUDGET) break;
    const idx = order.indexOf(t);
    if (idx === -1) continue;
    const others = files
      .filter((_, i) => i !== idx)
      .map((f) => f.content.trim())
      .filter(Boolean);
    assembled = others.join(SEP);
    tokens = estimateTokens(assembled);
    truncated = true;
  }

  // 그래도 초과하면 강제 절삭 (chars 단위 — 백엔드 정확 절삭은 추후)
  if (tokens > DEFAULT_TOKEN_BUDGET) {
    const maxChars = DEFAULT_TOKEN_BUDGET * 4;
    assembled = assembled.slice(0, maxChars);
    tokens = estimateTokens(assembled);
    truncated = true;
  }

  return {
    assembledText: assembled,
    tokenCount: tokens,
    tokenBudget: DEFAULT_TOKEN_BUDGET,
    wasTruncated: truncated,
  };
}

function isNotFound(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: string }).code === "ENOENT"
  );
}

export const PERSONA_TYPES = ORDER;
export const PERSONA_FILENAMES = FILE_MAP;
