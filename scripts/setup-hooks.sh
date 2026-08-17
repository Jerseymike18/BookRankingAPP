#!/usr/bin/env bash
#
# setup-hooks.sh — activate the versioned publish hooks for THIS clone.
#
# Run ONCE per clone. core.hooksPath is a per-repo git config value, not
# something that can be committed, so a fresh clone starts with the hooks
# inactive until this runs. Idempotent — safe to re-run any time.
#
#   scripts/setup-hooks.sh
#
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

git -C "$REPO_ROOT" config core.hooksPath scripts/hooks
chmod +x "$REPO_ROOT"/scripts/hooks/* \
         "$REPO_ROOT"/scripts/setup-hooks.sh \
         "$REPO_ROOT"/scripts/publish.sh \
         "$REPO_ROOT"/scripts/lint_docs.py \
         "$REPO_ROOT"/scripts/lint_constraints.py

echo "✓ Publish hooks active for this clone."
echo "  core.hooksPath = $(git -C "$REPO_ROOT" config core.hooksPath)"
echo "    • pre-commit — hard-constraint lint (lint_constraints.py), then a data"
echo "                   change auto-regenerates & stages the snapshot"
echo "    • commit-msg — verifies the 'engine-change: <reason>' marker against the"
echo "                   real message (pre-commit runs too early to see it)"
echo "    • pre-push   — hard-constraint lint per outgoing commit (the --no-verify"
echo "                   net), blocks a stale/invalid snapshot, then a doc-drift"
echo "                   audit (lint_docs.py --no-run-tests)"
echo
echo "  Publish from now on with just: git commit + git push  (or scripts/publish.sh)"
