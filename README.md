# The Reading Ledger

A personal book-tracking and prediction web app. For the app itself — stack,
scoring model, engine, and hard constraints — see [CLAUDE.md](CLAUDE.md). This
README covers **publishing to the live site**.

## Publishing (the short version)

The live site is a **static, backend-free snapshot** of `books.db`: the export
writes every read-only API response to `frontend/public/data/`, and the frontend
(built with `NEXT_PUBLIC_STATIC_DATA=1`) reads those files instead of calling the
local API. All site-visible edits happen through the local web app, which writes
`books.db`; the workbook (`BookRankingsNew.xlsx`) is **not** part of the publish
path.

Once the hooks are installed (below), publishing is just:

```bash
git commit -am "data: removed Dune"   # pre-commit regenerates + stages the snapshot
git push                              # pre-push validates it, then Vercel rebuilds
```

or the one-command wrapper:

```bash
scripts/publish.sh "removed Dune"     # regenerate → commit → push (message optional)
```

You never run `export-data` by hand or remember to validate anything — a commit
that changes book data **is** a publish, and no push can ship a stale or invalid
snapshot.

## One-time setup (run once per clone)

The hooks are versioned in `scripts/hooks/`, but git only uses them after you
point `core.hooksPath` at that directory. That setting is per-clone and cannot be
committed, so on a fresh clone run:

```bash
scripts/setup-hooks.sh
```

Verify with `git config core.hooksPath` → should print `scripts/hooks`. If the
hooks ever seem not to fire, this is the first thing to check.

## What the hooks do

- **pre-commit** — if a commit touches data-affecting files (`books.db`,
  `backend/`, `scripts/export_static_data.py`, or any top-level engine `*.py`),
  it regenerates the snapshot from the staged `books.db` and auto-stages it into
  the same commit. Non-data commits (docs, frontend tweaks) skip all of this and
  stay instant. Bypass with `git commit --no-verify` (the pre-push hook still
  catches a resulting stale snapshot).
- **pre-push** — the gate. If any outgoing commit touches book data or the
  snapshot, it re-runs the export in `--check` mode and **blocks the push** if the
  committed snapshot isn't byte-identical to a fresh export, or if validation
  fails. Pushing docs-only changes (or from a machine without Python) is
  unaffected.

Both hooks fail **loudly with the exact fix command** and never prompt.

## The export is deterministic

`scripts/export_static_data.py` guarantees that identical `books.db` state always
produces byte-identical output (generation timestamps stripped, JSON key order
canonical, atomic per-file writes, fetch-all-then-write-all). That determinism is
what makes staleness detection reliable.

```bash
python3 scripts/export_static_data.py             # full export → public/data/
python3 scripts/export_static_data.py --check      # exit 0 iff public/data/ is up to date
python3 scripts/export_static_data.py --verify-env # exit 0 iff the env can run the export
```

The live URL is not hardcoded; set `LIVE_URL` in your environment to have
`publish.sh` print it after a push.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Commit blocked: `export failed` | Engine/db error during export | Read the export error printed above the block; fix it locally, then commit again |
| Commit blocked: `books.db has unstaged changes` | `books.db` differs between the worktree and the index, so the snapshot wouldn't match the commit | `git add books.db` (to include the change) or `git stash push -- books.db` (to set it aside), then commit |
| Push blocked: `snapshot is STALE` | Committed with `--no-verify`, or edited `books.db` after committing it | Run `scripts/publish.sh` (regenerates, commits & pushes). Manual: `python3 scripts/export_static_data.py && git commit -am "data: refresh snapshot"` |
| Push blocked: `books.db or the snapshot has uncommitted changes` | Data files are dirty, so `--check` can't validate the committed state | Commit them (`scripts/publish.sh`) or `git stash`, then push |
| Push/commit blocked: missing `python3`/`node` or imports fail | Running from the wrong environment/machine | Activate the project's Python environment (`source .venv/bin/activate` or install `fastapi`/`httpx` + engine deps) and ensure `node` is on PATH — or push only non-data commits |
| Hooks not firing at all | `setup-hooks.sh` was never run on this clone | Run `scripts/setup-hooks.sh` once |
| Push warns: `BookRankingsNew.xlsx is newer than books.db` | The workbook was edited after `books.db` | Informational only — the site reads `books.db`, not the workbook. Not blocking |
