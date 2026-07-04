#!/usr/bin/env bash
#
# publish.sh — one command to publish book-data changes to the live site.
#
#   scripts/publish.sh "removed Dune"   # commit message → "data: removed Dune"
#   scripts/publish.sh                  # message defaults to "data: refresh snapshot"
#   scripts/publish.sh --no-push "…"    # commit locally, do not push
#
# Regenerates the static snapshot from books.db, commits books.db + the snapshot
# together, and pushes. Plain `git commit` + `git push` do the same thing (the
# pre-commit and pre-push hooks handle export + validation) — this is just the
# convenient wrapper, and the one-command recovery when a snapshot went stale.
#
set -euo pipefail

PUSH=1
MSG=""
for arg in "$@"; do
  case "$arg" in
    --no-push) PUSH=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) if [ -z "$MSG" ]; then MSG="$arg"; else MSG="$MSG $arg"; fi ;;
  esac
done
MSG="data: ${MSG:-refresh snapshot}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
DATA_DIR="frontend/public/data"

# 1) Only book-data paths may be dirty — never ship half-finished feature code
#    in a "publish". Combine unstaged + staged + untracked and flag anything
#    that is not books.db or under the snapshot tree.
dirty="$( { git -C "$REPO_ROOT" diff --name-only;
            git -C "$REPO_ROOT" diff --cached --name-only;
            git -C "$REPO_ROOT" ls-files --others --exclude-standard; } | sort -u )"
extras="$(printf '%s\n' "$dirty" | grep -vE "^(books\.db|${DATA_DIR}/)" | grep -v '^$' || true)"
if [ -n "$extras" ]; then
  echo "✗ publish: non-data files are dirty — refusing to publish half-finished work:" >&2
  printf '%s\n' "$extras" | sed 's/^/    /' >&2
  echo "  Fix: commit or stash them separately, then re-run scripts/publish.sh." >&2
  exit 1
fi

# 2) Regenerate the snapshot so it always matches books.db (this is also what
#    recovers a snapshot that went stale via a --no-verify commit). Fails loudly
#    with remediation if the Python environment can't produce it.
echo "▶ Regenerating snapshot from books.db…"
python3 "$REPO_ROOT/scripts/export_static_data.py"

# 3) Stage db + snapshot together (-A so removed books' score files are staged
#    as deletions).
git -C "$REPO_ROOT" add -A -- books.db "$DATA_DIR"

# 4) Nothing to publish if the staged tree already matches HEAD.
if git -C "$REPO_ROOT" diff --cached --quiet; then
  echo "✓ Already up to date — snapshot matches books.db, nothing to publish."
  exit 0
fi

# 5) Commit. The pre-commit hook re-runs the export (deterministic → no-op) and
#    re-stages, so the commit is guaranteed consistent even if hooks are active.
git -C "$REPO_ROOT" commit -m "$MSG"
echo "✓ Committed: $MSG"

# 6) Push — the pre-push hook validates the snapshot before it leaves.
if [ "$PUSH" -eq 1 ]; then
  echo "▶ Pushing…"
  git -C "$REPO_ROOT" push
  echo "✓ Pushed."
  if [ -n "${LIVE_URL:-}" ]; then
    echo "  Live: $LIVE_URL"
  fi
  echo "  Vercel will rebuild in ~1 min."
else
  echo "• --no-push set; commit is local. Push with: git push"
fi
