#!/usr/bin/env node
/**
 * BIZ-93 RejectInsightModal 시각 검증 스크립트.
 *
 * 목적: BIZ-93 의 destructive 토큰 (헤더 ShieldX 배경/foreground, footer "폐기 +
 * Blocklist 등록" 버튼, 선택된 기간 토글) 을 light + dark 양 테마에서 캡처.
 *
 * 동작 (BIZ-92 의 biz-92-screenshots.mjs 패턴 그대로):
 *  1) localhost 에 mock admin daemon 을 띄워 SuggestionQueuePanel 이 의존하는
 *     /admin/v1/memory/suggestions 등 엔드포인트에 fixture JSON 을 응답한다.
 *  2) 빌드된 Next.js admin UI 를 ADMIN_API_BASE/TOKEN 환경변수와 함께 띄운다.
 *  3) Playwright 로 /memory 진입 → "거절" 버튼 클릭 → RejectInsightModal 등장
 *     → 모달 영역만 잘라 PNG 저장.
 *
 * 출력: web/admin/screenshots-biz-93/{light,dark}/reject-modal.png
 *
 * 실행:
 *   cd web/admin
 *   npm install --no-save playwright @playwright/test
 *   npx playwright install chromium
 *   npm run build
 *   node scripts/biz-93-screenshots.mjs
 */
import http from "node:http";
import { spawn } from "node:child_process";
import { resolve, dirname } from "node:path";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const OUT_DIR = resolve(ROOT, "screenshots-biz-93");

const NEXT_PORT = 8092;
const MOCK_PORT = 8093;
const MOCK_TOKEN = "biz93-fixture-token";

// ---------- fixture data ----------------------------------------------------
// BIZ-93 모달 캡처에 필요한 최소 fixture — SuggestionQueuePanel 이 카드를 그릴 수
// 있을 정도의 suggestions 만 있으면 충분하다.

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
];

const MEMORY_INDEX = {
  entries: [
    {
      id: "0:0",
      section: "(root)",
      type: "user",
      text: "사용자는 한국어 존댓말을 선호한다.",
      raw: "- [user] 사용자는 한국어 존댓말을 선호한다.",
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

      if (path === "/admin/v1/memory/suggestions") {
        return jsonResponse(res, 200, {
          suggestions: SUGGESTIONS,
          total: SUGGESTIONS.length,
          pending_count: SUGGESTIONS.length,
        });
      }
      if (path === "/admin/v1/memory/index") {
        return jsonResponse(res, 200, MEMORY_INDEX);
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
      // 비치명적 endpoint 는 빈 JSON.
      jsonResponse(res, 200, {});
    });

    server.listen(MOCK_PORT, "127.0.0.1", () => {
      console.log(`[mock] listening on :${MOCK_PORT}`);
      resolveStart(server);
    });
  });
}

// ---------- next start (build 결과 사용) ------------------------------------

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
    }, 20000);
  });
}

// ---------- capture ---------------------------------------------------------

async function captureForTheme(browser, theme) {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 2, // retina-quality PNG.
  });
  await context.addInitScript((mode) => {
    window.localStorage.setItem("simpleclaw.admin.theme", mode);
  }, theme);
  const page = await context.newPage();

  await page.goto(`http://127.0.0.1:${NEXT_PORT}/memory`, {
    waitUntil: "domcontentloaded",
  });
  // 데이터 fetch + SuggestionQueuePanel 렌더 대기.
  await page.waitForSelector("text=검토 대기 큐", { timeout: 15000 });
  await page.waitForTimeout(500);

  // "거절" 버튼 — 첫 suggestion 카드의 거절 버튼을 누른다. 두 개의 suggestion 중
  // 첫 번째 (s-001) 의 거절 버튼이 첫 매치.
  const rejectButton = page.getByRole("button", { name: "거절" }).first();
  await rejectButton.click();

  // RejectInsightModal — alertdialog role + "차단 기간" legend 등장 대기.
  const dialog = page.getByRole("alertdialog");
  await dialog.waitFor({ timeout: 5000 });
  await page.waitForTimeout(400); // 페이드/포커스 안정화.

  const file = resolve(OUT_DIR, theme, "reject-modal.png");
  await mkdir(dirname(file), { recursive: true });
  // 모달은 화면 중앙. viewport 전체를 캡처하면 backdrop + 모달이 모두 담긴다 —
  // destructive 토큰의 시각 검증에는 충분.
  await page.screenshot({ path: file, fullPage: false });
  console.log(`[shot] ${file}`);

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
