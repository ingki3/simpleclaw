#!/usr/bin/env node
/**
 * Next.js 실행 wrapper.
 *
 * Multica 웹이 :3000을 점유하므로 SimpleClaw Admin은 기본 :3100으로 분리한다.
 * `PORT` 환경변수가 설정되어 있으면 그 값을 우선하고, 그렇지 않으면 3100을 사용한다.
 *
 * npm 환경변수 보간(`${PORT:-3100}`)은 macOS/Linux의 셸에서만 동작하므로
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

// 기본 3100, 외부 PORT 환경변수가 있으면 그 값을 사용 (예: PORT=3200 npm run dev)
const port = process.env.PORT && process.env.PORT.trim() !== "" ? process.env.PORT : "3100";

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
  shell: isWindows, // .cmd 실행을 위해 Windows에서만 셸 사용
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 0);
  }
});
