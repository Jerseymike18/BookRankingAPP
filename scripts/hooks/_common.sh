# shellcheck shell=bash
#
# _common.sh — shared helpers for the publish git hooks (pre-commit, pre-push).
#
# This file is SOURCED, never executed: the sourcing hook owns `set -euo
# pipefail`; here we only set vars and define functions. `exit` inside these
# functions therefore aborts the hook itself, which is the intended behavior —
# aborting with instructions is the ONLY failure mode (no prompts, no `read`).

REPO_ROOT="$(git rev-parse --show-toplevel)"
EXPORT_SCRIPT="$REPO_ROOT/scripts/export_static_data.py"
# Snapshot output tree, repo-relative (matches `git diff --name-only` output).
SNAPSHOT_PREFIX="frontend/public/data/"

# ── Is a changed path a data INPUT (i.e. can it alter the snapshot)? ───────────
# Categories the export depends on (brief Part 2):
#   • books.db                      — the source of truth the export reads
#   • backend/**                    — the API the export snapshots
#   • scripts/export_static_data.py — the exporter itself
#   • engine modules the backend imports
# The engine set is the transitive import closure of backend/main.py, which on
# 2026-07-03 is: compare_researchers, db_loader, db_write, hybrid_researcher,
# nonfiction_engine, nonfiction_research, predict_engine, reresearch_and_measure,
# research_layer, research_predict, validate_engine, views — all top-level *.py
# modules. Rather than hand-maintain that list (under-inclusion would silently
# ship a stale snapshot), we match ANY top-level *.py. Over-inclusion only ever
# costs a redundant, deterministic re-export — which stages nothing new.
_path_is_data_input() {
  case "$1" in
    books.db) return 0 ;;
    backend/*) return 0 ;;
    scripts/export_static_data.py) return 0 ;;
    */*) return 1 ;;   # any other nested path is not a top-level engine module
    *.py) return 0 ;;  # a top-level python module (engine, err toward inclusion)
  esac
  return 1
}

# Read a newline-separated path list on stdin; exit 0 if ANY is a data input.
paths_have_data_input() {
  local path
  while IFS= read -r path; do
    _path_is_data_input "$path" && return 0
  done
  return 1
}

# Read a newline-separated path list on stdin; exit 0 if ANY change would make
# the committed snapshot stale — either a data INPUT or the snapshot output tree
# itself (a hand-edited snapshot must still be validated on push).
paths_need_export() {
  local path
  while IFS= read -r path; do
    _path_is_data_input "$path" && return 0
    case "$path" in
      "$SNAPSHOT_PREFIX"*) return 0 ;;
    esac
  done
  return 1
}

# data_files_changed <git-diff-selector...> — 0 if the diff touches a data input.
# Honors the brief's signature: `data_files_changed --cached` (staged changes)
# or `data_files_changed "<remote-sha>..<local-sha>"` (a push range).
data_files_changed() {
  git -C "$REPO_ROOT" diff --name-only "$@" -- | paths_have_data_input
}

# ── require_tools — fail LOUDLY if this environment cannot produce a snapshot ──
# Verifies python3 + node exist and the export's imports resolve. Never silently
# skips: quietly publishing stale data is the failure mode this whole hook set
# exists to prevent. Every message states the exact command that fixes it.
require_tools() {
  local missing=0
  if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ publish hook: python3 not found on PATH." >&2
    echo "  Fix: install Python 3 or activate the project environment, then retry." >&2
    missing=1
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "✗ publish hook: node not found on PATH." >&2
    echo "  Fix: install Node.js (e.g. 'nvm use'), then retry." >&2
    missing=1
  fi
  if [ "$missing" -ne 0 ]; then
    exit 1
  fi
  # Imports must resolve. --verify-env prints the exact remediation on failure.
  if ! python3 "$EXPORT_SCRIPT" --verify-env; then
    exit 1
  fi
}
