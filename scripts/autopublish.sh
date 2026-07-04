#!/usr/bin/env bash
#
# autopublish.sh — watch books.db and SILENTLY publish edits to the live site.
#
# Every book change the local web app writes to books.db is committed (the
# snapshot regenerates) and pushed, so the live site updates with no git
# commands. It debounces rapid edits into one publish and only ever touches
# books.db + the snapshot — if other files are dirty it SKIPS the cycle rather
# than sweep unrelated work into a data commit.
#
#   scripts/autopublish.sh                       # watch forever (Ctrl-C to stop)
#   scripts/autopublish.sh --once                # publish one pending change, exit
#   AUTOPUBLISH_POLL=3 AUTOPUBLISH_QUIET=5 scripts/autopublish.sh
#
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
EXPORT="$REPO_ROOT/scripts/export_static_data.py"
DATA_DIR="frontend/public/data"
POLL="${AUTOPUBLISH_POLL:-3}"      # seconds between polls
QUIET="${AUTOPUBLISH_QUIET:-5}"    # seconds the db must be still before publishing

log() { printf '[autopublish %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

db_hash() { shasum "$REPO_ROOT/books.db" 2>/dev/null | awk '{print $1}'; }

# 0 iff the only dirty paths are books.db / the snapshot (safe to auto-commit).
only_data_dirty() {
  local dirty extras
  dirty="$( { git -C "$REPO_ROOT" diff --name-only;
              git -C "$REPO_ROOT" diff --cached --name-only;
              git -C "$REPO_ROOT" ls-files --others --exclude-standard; } | sort -u )"
  extras="$(printf '%s\n' "$dirty" | grep -vE "^(books\.db|${DATA_DIR}/)" | grep -v '^$' || true)"
  [ -z "$extras" ]
}

# Publish a pending books.db change, if any. Always returns 0 — it logs and
# bows out on every non-fatal condition so the watch loop never dies.
publish_once() {
  local commit_out push_out
  if git -C "$REPO_ROOT" diff --quiet HEAD -- books.db; then
    return 0   # books.db matches the last commit — nothing to publish
  fi
  if ! only_data_dirty; then
    log "skip — non-data files are dirty; commit/stash them and edits will publish again."
    return 0
  fi
  log "publishing books.db change…"
  # Regenerate defensively so this works even if the git hooks aren't installed;
  # when they are, pre-commit re-runs it deterministically (a harmless no-op).
  if ! python3 "$EXPORT" >/dev/null 2>&1; then
    log "export failed — not committing. Run 'python3 scripts/export_static_data.py' to see why. Will retry on next change."
    return 0
  fi
  if ! git -C "$REPO_ROOT" add -A -- books.db "$DATA_DIR"; then
    log "git add failed — will retry on next change."
    return 0
  fi
  if git -C "$REPO_ROOT" diff --cached --quiet; then
    log "already up to date — nothing to commit."
    return 0
  fi
  # Capture hook chatter (pre-commit export summary, pre-push --check) so the
  # daemon stays quiet on success and only surfaces details when something fails.
  if ! commit_out="$(git -C "$REPO_ROOT" commit -m "data: auto-publish $(date '+%Y-%m-%d %H:%M:%S')" 2>&1)"; then
    log "commit blocked — will retry on next change. Details:"
    printf '%s\n' "$commit_out" | sed 's/^/    /' >&2
    return 0
  fi
  if push_out="$(git -C "$REPO_ROOT" push 2>&1)"; then
    log "✓ published & pushed — Vercel will rebuild in ~1 min."
  else
    log "⚠ committed locally but push failed (offline or remote ahead). Will push with the next change, or run: git push. Details:"
    printf '%s\n' "$push_out" | sed 's/^/    /' >&2
  fi
  return 0
}

# ── one-shot mode (manual / testing / cron) ──────────────────────────────────
if [ "${1:-}" = "--once" ]; then
  publish_once
  exit 0
elif [ "${1:-}" != "" ]; then
  echo "usage: $0 [--once]" >&2
  exit 2
fi

# ── watch mode ───────────────────────────────────────────────────────────────
trap 'log "stopped."; exit 0' INT TERM
log "watching books.db (poll ${POLL}s, settle ${QUIET}s). Ctrl-C to stop."
publish_once   # flush anything already pending at startup
last="$(db_hash)"
while true; do
  sleep "$POLL"
  cur="$(db_hash)"
  if [ "$cur" != "$last" ]; then
    # Debounce: wait until the db stops changing so a burst of edits is one push.
    while true; do
      prev="$cur"
      sleep "$QUIET"
      cur="$(db_hash)"
      [ "$cur" = "$prev" ] && break
    done
    last="$cur"
    publish_once
  fi
done
