#!/usr/bin/env node
/**
 * BIZ-92 시각 검증 스크립트.
 *
 * 목적: /memory/insights 의 4 탭 + light/dark 테마 스크린샷을 일괄 캡처.
 *
 * 동작:
 *  1) localhost 에 mock admin daemon 을 띄운다 (Node http) — 모든 /admin/v1/...
 *     호출에 대해 합리적 fixture JSON 응답.
 *  2) Next.js (이미 빌드된 admin UI) 를 ADMIN_API_BASE/TOKEN 환경변수와 함께 띄운다.
 *  3) Playwright 로 8088 에 접속, 각 탭/테마 조합으로 PNG 저장.
 *
 * 실행:
 *   cd web/admin && node scripts/biz-92-screenshots.mjs
 *
 * 출력: web/admin/screenshots-biz-92/{light,dark}/{review,active,archive,blocklist,memory-entry}.png
 *
 * 데몬 스택을 띄우지 않고 페이지 골격만 검증하기 위한 도구 — 실제 운영 daemon 과
 * 무관한 fixture 로 동작한다.
 */
import http from "node:http";
import { spawn } from "node:child_process";
import { resolve, dirname } from "node:path";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const OUT_DIR = resolve(ROOT, "screenshots-biz-92");

const NEXT_PORT = 8090;
const MOCK_PORT = 8091;
const MOCK_TOKEN = "biz92-fixture-token";

// ---------- fixture data ----------------------------------------------------

const NOW = "2026-05-04T10:00:00Z";

const SUGGESTIONS = [
  {
    id: "s-001",
    topic: "한국어 존댓말 선호",
    text: "사용자는 모든 응답을 한국어 존댓말로 받기를 선호한다.",
    edited_text: null,
    applied_text: "사용자는 모든 응답을 한국어 존댓말로 받기를 선호한다.",
    confidence: 0.84,
    evidence_count: 7,
    source_msg_ids: ["m1", "m2", "m3"],
    start_msg_id: "m1",
    end_msg_id: "m3",
    status: "pending",
    reject_reason: null,
    created_at: "2026-05-03T22:10:00Z",
    updated_at: "2026-05-04T01:25:00Z",
  },
  {
    id: "s-002",
    topic: "릿코드 풀이 톤",
    text: "릿코드 문제 풀이는 단계별 설명 + 시간복잡도 한 줄 요약을 좋아한다.",
    edited_text: null,
    applied_text: "릿코드 문제 풀이는 단계별 설명 + 시간복잡도 한 줄 요약을 좋아한다.",
    confidence: 0.55,
    evidence_count: 3,
    source_msg_ids: ["m4", "m5"],
    start_msg_id: "m4",
    end_msg_id: "m5",
    status: "pending",
    reject_reason: null,
    created_at: "2026-05-03T20:00:00Z",
    updated_at: "2026-05-03T20:30:00Z",
  },
  {
    id: "s-003",
    topic: "cron 자동 실행 알림",
    text: "cron 알림 타임스탬프를 본문에 자주 포함한다.",
    edited_text: null,
    applied_text: "cron 알림 타임스탬프를 본문에 자주 포함한다.",
    confidence: 0.22,
    evidence_count: 2,
    source_msg_ids: ["m6"],
    start_msg_id: "m6",
    end_msg_id: "m6",
    status: "pending",
    reject_reason: null,
    created_at: "2026-05-03T15:00:00Z",
    updated_at: "2026-05-03T15:05:00Z",
  },
];

const ACTIVE_INSIGHTS = [
  {
    topic: "다크 테마 기본",
    text: "Admin UI 작업에서는 항상 다크 테마부터 검증한다.",
    evidence_count: 12,
    confidence: 0.91,
    first_seen: "2026-04-10T00:00:00Z",
    last_seen: "2026-05-02T18:00:00Z",
    start_msg_id: null,
    end_msg_id: null,
    source_msg_ids: [],
    archived_at: null,
  },
  {
    topic: "PR DoD 자가체크",
    text: "PR 결과 코멘트는 DoD 항목별 done/partial/punted 상태를 명시한다.",
    evidence_count: 5,
    confidence: 0.62,
    first_seen: "2026-04-25T00:00:00Z",
    last_seen: "2026-05-01T11:00:00Z",
    start_msg_id: null,
    end_msg_id: null,
    source_msg_ids: [],
    archived_at: null,
  },
];

const ARCHIVED_INSIGHTS = [
  {
    topic: "오래된 빌드 도구 선호",
    text: "초기에 webpack 5 + ts-loader 조합을 선호했다.",
    evidence_count: 4,
    confidence: 0.48,
    first_seen: "2026-01-15T00:00:00Z",
    last_seen: "2026-02-10T09:00:00Z",
    start_msg_id: null,
    end_msg_id: null,
    source_msg_ids: [],
    archived_at: "2026-03-20T00:00:00Z",
  },
];

const BLOCKLIST = [
  {
    topic: "Cron 노이즈",
    topic_key: "cron_noise",
    reason: "cron/recipe 자동 메시지에서 추출된 1회성 인사이트 — 학습 가치 없음.",
    blocked_at: "2026-04-15T10:30:00Z",
  },
  {
    topic: "주식 농담",
    topic_key: "stock_joke",
    reason: "일회성 농담",
    blocked_at: "2026-04-01T08:00:00Z",
  },
];

const DREAMING_STATUS = {
  last_run: {
    id: "run-2026-05-03",
    started_at: "2026-05-03T22:00:00Z",
    ended_at: "2026-05-03T22:04:30Z",
    duration_seconds: 270,
    input_msg_count: 142,
    generated_insight_count: 5,
    rejected_count: 1,
    error: null,
    skip_reason: null,
    status: "success",
    details: {},
  },
  last_successful_run: null,
  next_run: "2026-05-04T22:00:00Z",
  overnight_hour: 22,
  idle_threshold_seconds: 600,
  trigger_blockers: [],
  trigger_message: null,
  kpi_7d: null,
  rejection: { reviewed: 12, rejected: 2, rate: 0.166 },
  metrics_enabled: true,
};

const MEMORY_INDEX = {
  entries: [
    {
      id: "0:0",
      section: "(root)",
      type: "user",
      text: "사용자는 한국어 존댓말을 선호한다.",
      raw: "- [user] 사용자는 한국어 존댓말을 선호한다.",
    },
    {
      id: "0:1",
      section: "(root)",
      type: "feedback",
      text: "테스트에서 mock DB 사용 금지.",
      raw: "- [feedback] 테스트에서 mock DB 사용 금지.",
    },
  ],
  stats: {
    totalMessages: 1842,
    diskBytes: 12_345_678,
    lastDreamingAt: "2026-05-03T22:04:30Z",
  },
  file: { sizeBytes: 4096, updatedAt: "2026-05-03T22:04:30Z" },
  dreaming: {
    running: false,
    started_at: null,
    finished_at: "2026-05-03T22:04:30Z",
    error: null,
    step: null,
  },
};

// ---------- mock daemon -----------------------------------------------------

function jsonResponse(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

function startMockDaemon() {
  return new Promise((resolveStart) => {
    const server = http.createServer((req, res) => {
      const auth = req.headers["authorization"] || "";
      if (!auth.startsWith("Bearer ")) {
        return jsonResponse(res, 401, { error: "missing token" });
      }

      const url = new URL(req.url, `http://127.0.0.1:${MOCK_PORT}`);
      const path = url.pathname;
      const status = url.searchParams.get("status") || "pending";

      if (path === "/admin/v1/memory/suggestions") {
        return jsonResponse(res, 200, {
          suggestions: SUGGESTIONS,
          total: SUGGESTIONS.length,
          pending_count: SUGGESTIONS.length,
        });
      }
      if (path === "/admin/v1/memory/insights") {
        const list =
          status === "all"
            ? [...ACTIVE_INSIGHTS, ...ARCHIVED_INSIGHTS]
            : status === "archived"
              ? ARCHIVED_INSIGHTS
              : ACTIVE_INSIGHTS;
        return jsonResponse(res, 200, {
          insights: list,
          total: list.length,
          active_count: ACTIVE_INSIGHTS.length,
          archived_count: ARCHIVED_INSIGHTS.length,
        });
      }
      if (path === "/admin/v1/memory/blocklist") {
        return jsonResponse(res, 200, {
          entries: BLOCKLIST,
          total: BLOCKLIST.length,
        });
      }
      if (path === "/admin/v1/memory/dreaming/status") {
        return jsonResponse(res, 200, DREAMING_STATUS);
      }
      if (path === "/admin/v1/memory/dreaming/runs") {
        return jsonResponse(res, 200, {
          runs: [DREAMING_STATUS.last_run],
          total: 1,
        });
      }
      if (path === "/admin/v1/memory/index") {
        return jsonResponse(res, 200, MEMORY_INDEX);
      }
      // 기본 — 비치명적 endpoint 는 빈 JSON.
      jsonResponse(res, 200, {});
    });

    server.listen(MOCK_PORT, "127.0.0.1", () => {
      console.log(`[mock] listening on :${MOCK_PORT}`);
      resolveStart(server);
    });
  });
}

// ---------- next dev server (build 결과 사용 — start) -----------------------

function startNextServer() {
  return new Promise((resolveStart, reject) => {
    const child = spawn(
      resolve(ROOT, "node_modules/.bin/next"),
      ["start", "-p", String(NEXT_PORT)],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          ADMIN_API_BASE: `http://127.0.0.1:${MOCK_PORT}`,
          ADMIN_API_TOKEN: MOCK_TOKEN,
          PORT: String(NEXT_PORT),
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let resolved = false;
    child.stdout.on("data", (chunk) => {
      const text = chunk.toString();
      process.stdout.write(`[next] ${text}`);
      if (!resolved && /Ready in|Local:/.test(text)) {
        resolved = true;
        // 서버가 listen 후에도 첫 컴파일이 일어나므로 약간 더 대기.
        setTimeout(() => resolveStart(child), 1000);
      }
    });
    child.stderr.on("data", (chunk) => {
      process.stderr.write(`[next-err] ${chunk}`);
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (!resolved) reject(new Error(`next exited early code=${code}`));
    });
    setTimeout(() => {
      if (!resolved) {
        resolved = true;
        resolveStart(child);
      }
    }, 15000);
  });
}

// ---------- capture ---------------------------------------------------------

const TABS = [
  { value: "review", label: "Review" },
  { value: "active", label: "Active" },
  { value: "archive", label: "Archive" },
  { value: "blocklist", label: "Blocklist" },
];

async function capture(page, theme, tab) {
  // tab 클릭 후 짧게 대기.
  if (tab) {
    await page.getByRole("tab", { name: tab.label }).click();
    await page.waitForTimeout(200);
  }
  const file = resolve(
    OUT_DIR,
    theme,
    `${tab ? tab.value : "memory-entry"}.png`,
  );
  await mkdir(dirname(file), { recursive: true });
  await page.screenshot({ path: file, fullPage: true });
  console.log(`[shot] ${file}`);
}

async function captureForTheme(browser, theme) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  await context.addInitScript((mode) => {
    window.localStorage.setItem("simpleclaw.admin.theme", mode);
  }, theme);
  const page = await context.newPage();

  // /memory 진입 카드 캡처.
  await page.goto(`http://127.0.0.1:${NEXT_PORT}/memory`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(800);
  await capture(page, theme, null);

  // /memory/insights 4 탭 캡처.
  await page.goto(`http://127.0.0.1:${NEXT_PORT}/memory/insights`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(800);
  for (const tab of TABS) {
    await capture(page, theme, tab);
  }
  await context.close();
}

// ---------- main -----------------------------------------------------------

const mock = await startMockDaemon();
const next = await startNextServer();

let exitCode = 0;
try {
  const browser = await chromium.launch();
  for (const theme of ["light", "dark"]) {
    await captureForTheme(browser, theme);
  }
  await browser.close();
  console.log("done.");
} catch (e) {
  console.error("capture failed:", e);
  exitCode = 1;
} finally {
  next.kill("SIGTERM");
  mock.close();
}

process.exit(exitCode);
