#!/usr/bin/env bash
#
# publish.sh — one-command publish loop for the read-only public site.
#
#   1. Regenerate the static snapshot (scripts/export_static_data.py, which
#      itself hard-fails on slug-parity mismatch or score/book count drift).
#   2. Guard against a catastrophic shrink (a broken/partial export or the wrong
#      books.db) before anything is committed.
#   3. Commit only the data snapshot and push — so Vercel auto-rebuilds.
#
# The export is deterministic: if books.db hasn't changed there's nothing to
# publish, and this exits cleanly without an empty commit.
#
# Usage:
#   scripts/publish.sh            # export, commit, push
#   scripts/publish.sh --no-push  # export + commit, but stop before pushing
#
set -euo pipefail

PUSH=1
for arg in "$@"; do
  case "$arg" in
    --no-push) PUSH=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)"
DATA_DIR="$ROOT/frontend/public/data"

# Baseline = number of snapshot files currently committed (last publish).
prev_count="$(git -C "$ROOT" ls-files "$DATA_DIR" | wc -l | tr -d ' ')"

echo "▶ Regenerating snapshot…"
( cd "$ROOT/frontend" && npm run export-data )

new_count="$(find "$DATA_DIR" -type f | wc -l | tr -d ' ')"

# Safety net: refuse to publish an implausibly small snapshot. A real book
# removal barely moves the count; losing >50% means a broken export or the
# wrong database. (Skip the ratio check on the very first publish.)
if [ "$new_count" -eq 0 ]; then
  echo "✗ Export produced 0 files — refusing to publish." >&2
  exit 1
fi
if [ "$prev_count" -gt 0 ] && [ "$((new_count * 2))" -lt "$prev_count" ]; then
  echo "✗ Snapshot shrank from $prev_count to $new_count files (>50% drop) — refusing to publish." >&2
  echo "  Re-run scripts/export_static_data.py and inspect the diff by hand if this is intentional." >&2
  exit 1
fi

git -C "$ROOT" add "$DATA_DIR"

if git -C "$ROOT" diff --cached --quiet -- "$DATA_DIR"; then
  echo "✓ Snapshot unchanged (books.db hasn't moved) — nothing to publish."
  exit 0
fi

# Book counts for a descriptive commit message.
read -r fic nonfic <<EOF
$(python3 - "$DATA_DIR" <<'PY'
import json, sys
d = sys.argv[1]
def n(p):
    try:
        return len(json.load(open(f"{d}/{p}"))["books"])
    except Exception:
        return "?"
print(n("fiction/books.json"), n("nonfiction/books.json"))
PY
)
EOF

msg="data: refresh snapshot (${fic} fiction / ${nonfic} nonfiction)"
git -C "$ROOT" commit -q -m "$msg"
echo "✓ Committed: $msg"

if [ "$PUSH" -eq 1 ]; then
  echo "▶ Pushing…"
  git -C "$ROOT" push
  echo "✓ Pushed — Vercel will rebuild."
else
  echo "• --no-push set; commit is local. Push with: git push"
fi
