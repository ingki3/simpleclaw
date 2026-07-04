#!/usr/bin/env bash
# SimpleClaw 코드 관계 그래프를 code-only/zero-token 방식으로 갱신한다.
# Graphify의 assistant-instruction 자동 설치는 사용하지 않고, repo 산출물만 재생성한다.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/dev/update_graphify.sh [--mode full|update] [--target PATH] [--skip-cluster]

Defaults:
  --mode update
  --target .

SimpleClaw policy:
  - Run from the repository root.
  - Default target is the repository root so src/simpleclaw, tests/unit, scripts, and web code stay in one shared graph.
  - Clustering uses --no-label --no-viz to avoid LLM/API calls and large HTML artifacts.
  - Commit graphify-out/GRAPH_REPORT.md, graphify-out/graph.json, and graphify-out/manifest.json only.

Examples:
  scripts/dev/update_graphify.sh --mode full
  scripts/dev/update_graphify.sh --target src/simpleclaw
  SIMPLECLAW_GRAPHIFY_INSTALL=1 scripts/dev/update_graphify.sh --mode full
USAGE
}

mode="update"
target="."
skip_cluster=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      mode="${2:-}"; shift 2 ;;
    --target)
      target="${2:-}"; shift 2 ;;
    --skip-cluster)
      skip_cluster=1; shift ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ "$mode" != "full" && "$mode" != "update" ]]; then
  echo "--mode must be 'full' or 'update'" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$repo_root"

if ! command -v graphify >/dev/null 2>&1; then
  if [[ "${SIMPLECLAW_GRAPHIFY_INSTALL:-0}" == "1" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
      echo "graphify is missing and uv is not installed; install with: uv tool install graphifyy" >&2
      exit 127
    fi
    uv tool install graphifyy
  else
    echo "graphify CLI is missing. Install once with: uv tool install graphifyy" >&2
    echo "Or rerun with SIMPLECLAW_GRAPHIFY_INSTALL=1 to let this script install it." >&2
    exit 127
  fi
fi

if [[ ! -e "$target" ]]; then
  echo "target path does not exist: $target" >&2
  exit 2
fi

if [[ "$mode" == "full" ]]; then
  rm -rf graphify-out
fi

# GRAPHIFY_FORCE avoids refusing legitimate node-count decreases after refactors/deletions.
export GRAPHIFY_FORCE=1
export SIMPLECLAW_GRAPHIFY_TARGET="$target"

echo "[graphify] repo=$repo_root"
echo "[graphify] mode=$mode target=$target"
echo "[graphify] running code-only update (no LLM needed)"
graphify update "$target" --no-cluster

# Graphify writes graphify-out/ under the path passed to `update`.
# SimpleClaw keeps the shared artifact at the repo root, so focused targets are normalized here.
if [[ "$target" != "." && "$target" != "./" && -s "$target/graphify-out/graph.json" ]]; then
  rm -rf graphify-out
  mv "$target/graphify-out" graphify-out
fi

if [[ "$skip_cluster" != "1" ]]; then
  echo "[graphify] regenerating report without LLM labels or HTML visualization"
  graphify cluster-only . --graph graphify-out/graph.json --no-label --no-viz
fi

if [[ ! -s graphify-out/graph.json ]]; then
  echo "missing graphify-out/graph.json after update" >&2
  exit 1
fi
if [[ ! -s graphify-out/GRAPH_REPORT.md ]]; then
  echo "missing graphify-out/GRAPH_REPORT.md after update" >&2
  exit 1
fi

python3 - <<'PY'
from __future__ import annotations
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import os

root = Path.cwd()
graph = root / "graphify-out" / "graph.json"
data = json.loads(graph.read_text())
links = data.get("links") or data.get("edges") or []
try:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
except Exception:
    commit = None
manifest = {
    "tool": "graphifyy",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "git_commit": commit,
    "target": os.environ.get("SIMPLECLAW_GRAPHIFY_TARGET", "."),
    "mode": "code-only",
    "cluster": "--no-label --no-viz",
    "nodes": len(data.get("nodes", [])),
    "edges": len(links),
    "tracked_outputs": [
        "graphify-out/GRAPH_REPORT.md",
        "graphify-out/graph.json",
        "graphify-out/manifest.json",
    ],
    "notes": [
        "EXTRACTED edges are navigation evidence; INFERRED edges are hints and must be verified in source.",
        "Graphify is a development/review aid, not a CI gate.",
    ],
}
(root / "graphify-out" / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
)
print(f"[graphify] wrote manifest: {manifest['nodes']} nodes, {manifest['edges']} edges")
PY

echo "[graphify] done"
