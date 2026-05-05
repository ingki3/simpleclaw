#!/usr/bin/env node
/**
 * Next.js 실행 wrapper — Admin 2.0.
 *
 * 동일 머신에서 다음 포트가 이미 점유될 수 있으므로 분리한다.
 * - :3000 — Multica 웹
 * - :8088 — 기존 Admin (web/admin)
 * 따라서 Admin 2.0은 기본 :8089를 사용한다. `PORT` 환경변수로 오버라이드 가능.
 *
 * npm 환경변수 보간(`${PORT:-8089}`)은 macOS/Linux의 셸에서만 동작하므로
 * Windows·CI 환경에서도 동일하게 동작하도록 작은 Node 래퍼로 처리한다.
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const command = process.argv[2];
if (command !== "dev" && command !== "start") {
  console.error("Usage: run-next.mjs <dev|start>");
  process.exit(1);
}

const port = process.env.PORT && process.env.PORT.trim() !== "" ? process.env.PORT : "8089";

const __dirname = dirname(fileURLToPath(import.meta.url));
const isWindows = process.platform === "win32";
const nextBin = resolve(
  __dirname,
  "..",
  "node_modules",
  ".bin",
  isWindows ? "next.cmd" : "next",
);

const child = spawn(nextBin, [command, "-p", port], {
  stdio: "inherit",
  env: { ...process.env, PORT: port },
  shell: isWindows,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 0);
  }
});
