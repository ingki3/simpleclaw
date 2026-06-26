#!/usr/bin/env bash
# Shared Graphify hook helper for SimpleClaw.
# Hooks are advisory by default. Set SIMPLECLAW_GRAPHIFY_AUTO=1 to regenerate graphify-out automatically.
set -euo pipefail

hook_name="${1:-unknown}"
range_start="${2:-}"
range_end="${3:-HEAD}"

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$repo_root"

is_relevant_path() {
  case "$1" in
    src/simpleclaw/*|tests/unit/*|scripts/*|web/admin/*|prompts/system/*|AGENTS.md|.gitignore|scripts/dev/update_graphify.sh|.githooks/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

changed_files=""
if [[ -n "$range_start" && -n "$range_end" ]] && git rev-parse --verify -q "$range_start" >/dev/null && git rev-parse --verify -q "$range_end" >/dev/null; then
  changed_files="$(git diff --name-only "$range_start" "$range_end" || true)"
elif [[ "$hook_name" == "post-commit" ]]; then
  changed_files="$(git diff-tree --no-commit-id --name-only -r HEAD || true)"
fi

needs_update=0
if [[ -z "$changed_files" ]]; then
  if [[ ! -s graphify-out/graph.json || ! -s graphify-out/GRAPH_REPORT.md ]]; then
    needs_update=1
  fi
else
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if is_relevant_path "$path"; then
      needs_update=1
      break
    fi
  done <<< "$changed_files"
fi

if [[ "$needs_update" != "1" ]]; then
  exit 0
fi

if [[ "${SIMPLECLAW_GRAPHIFY_AUTO:-0}" == "1" ]]; then
  echo "[simpleclaw graphify hook] $hook_name: relevant change detected; updating graphify-out"
  scripts/dev/update_graphify.sh --mode update
  if ! git diff --quiet -- graphify-out/GRAPH_REPORT.md graphify-out/graph.json graphify-out/manifest.json; then
    echo "[simpleclaw graphify hook] graphify-out changed. Review and commit these files."
  fi
else
  cat >&2 <<'MSG'
[simpleclaw graphify hook] relevant code/instruction changes detected.
Run this before handoff/PR when you want shared graph artifacts updated:
  scripts/dev/update_graphify.sh --mode update

To let hooks regenerate automatically in this clone:
  git config core.hooksPath .githooks
  export SIMPLECLAW_GRAPHIFY_AUTO=1
MSG
fi
