#!/usr/bin/env bash
#
# lint_commits.sh — run the hard-constraint lint on EACH commit in a selection.
#
# The single implementation of "how we constraint-lint a set of commits", shared
# by scripts/hooks/pre-push (the local --no-verify net) and the constraints CI
# workflow (the net for `git push --no-verify`, which skips the hook too). Having
# one copy is the point: this gate reads commit MESSAGES, so a subtle difference
# between two implementations would be a silent hole rather than a visible bug.
#
# WHY PER COMMIT, not one lint over the whole range: the engine-immutability
# check is satisfied by an 'engine-change: <reason>' line in the commit message.
# Linted as a range, ONE commit's marker would license a DIFFERENT commit's
# unexplained engine edit. Per commit, each engine change carries its own
# justification.
#
# Usage:  scripts/lint_commits.sh <rev-list args...>
#   scripts/lint_commits.sh "$remote_sha..$local_sha"
#   scripts/lint_commits.sh "$sha" --not --remotes      # new branch
#
# Exit 0 = clean (warnings do not block), 1 = at least one commit has an ERROR.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
LINTER="$REPO_ROOT/scripts/lint_constraints.py"

# Above this many commits, lint the newest MAX and say which ones were skipped.
# A bounded check that announces its bound beats an unbounded one that times out.
MAX_LINT="${LINT_COMMITS_MAX:-50}"

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/lint_commits.sh <rev-list args...>" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "⚠ lint_commits: python3 not found — constraint lint SKIPPED." >&2
  exit 0
fi

commits="$(git -C "$REPO_ROOT" rev-list "$@" 2>/dev/null || true)"
if [ -z "$commits" ]; then
  exit 0
fi

n_out="$(printf '%s\n' "$commits" | grep -c . || true)"
if [ "$n_out" -gt "$MAX_LINT" ]; then
  echo "⚠ lint_commits: $n_out commits in range — linting the newest $MAX_LINT." >&2
  echo "  The older $((n_out - MAX_LINT)) are NOT checked here; for a full sweep run" >&2
  echo "  'python3 scripts/lint_constraints.py --range <base>..HEAD'." >&2
  commits="$(printf '%s\n' "$commits" | head -n "$MAX_LINT")"
fi

failed=0
for sha in $commits; do
  # A root commit has no parent, so there is no A..B range to diff against.
  # Report it rather than skipping silently.
  if ! git -C "$REPO_ROOT" rev-parse -q --verify "$sha^1" >/dev/null 2>&1; then
    echo "⚠ lint_commits: $(git -C "$REPO_ROOT" log --format=%h -1 "$sha") is a root commit — not linted." >&2
    continue
  fi
  if ! python3 "$LINTER" --range "$sha~1..$sha" >/dev/null 2>&1; then
    # Re-run visibly so each finding prints with its own fix.
    echo "── constraint violations in $(git -C "$REPO_ROOT" log --format='%h %s' -1 "$sha") ──" >&2
    python3 "$LINTER" --range "$sha~1..$sha" >&2 || true
    failed=1
  fi
done

exit "$failed"
