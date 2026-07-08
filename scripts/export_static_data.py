#!/usr/bin/env python3
"""Snapshot the read-only API into static JSON for a hosted, backend-free deploy.

Runs the existing FastAPI app in-process (no uvicorn) via ``TestClient`` and
writes every read-only GET endpoint's response to ``frontend/public/data/``.
The frontend, built with ``NEXT_PUBLIC_STATIC_DATA=1``, reads those files
instead of calling ``localhost:8000`` — see ``frontend/lib/api.ts``.

Local dev is untouched: this only writes files; it never mutates ``books.db``
and never touches any endpoint that writes state or spends Anthropic tokens
(predict/research, discover, lookup, queue writes, recommendation meta, LOO).

Determinism contract
---------------------
Identical ``books.db`` state ALWAYS produces byte-identical output. This is what
lets the git hooks detect a stale snapshot reliably. It rests on three things:
  * generation artifacts (``generated_at`` and friends) are stripped in this
    layer — see ``_NONDETERMINISTIC_KEYS`` — so wall-clock time never leaks in;
  * JSON is serialized with ``sort_keys=True`` (object key order is canonical;
    list order — e.g. ranked books — is meaningful and passes through untouched);
  * every response is fetched into memory BEFORE anything is written, and each
    file is written atomically (tmp + ``os.replace``), so a crash mid-run can
    never leave a half-written or partially-updated snapshot on disk.

Usage (from the project root or frontend/):
    python3 scripts/export_static_data.py            # full export → public/data/
    python3 scripts/export_static_data.py --check     # is public/data/ up to date?
    python3 scripts/export_static_data.py --verify-env # can this env run the export?
    npm run export-data                               # from frontend/ (full export)

``--check`` regenerates the snapshot into a temp dir and exits 0 if it is
byte-identical to ``frontend/public/data/``, 1 (listing the differing files) if
not. It NEVER modifies ``public/data/`` — it is the read-only gate the pre-push
hook uses. ``--verify-env`` exits 0 iff every import the export needs resolves.

Dependencies: stdlib + the backend's own deps. ``TestClient`` needs ``httpx``,
which FastAPI already pulls in — no extra install for this repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "frontend" / "public" / "data"

# Keys that are generation artifacts, not data. They are stripped from every
# payload so identical books.db state yields byte-identical output regardless of
# when the export runs. Only ``generated_at`` (written by compare_researchers.py
# into compare_researchers_result.json, surfaced via
# /api/calibration/researcher-comparison) currently reaches the snapshot.
# NOTE: ``logged_at`` in /api/delta-log is a STORED per-row value — real data,
# deterministic w.r.t. the DB — and is deliberately NOT stripped.
_NONDETERMINISTIC_KEYS = frozenset({"generated_at"})


# ── Slug rule (MUST stay identical to slugify() in frontend/lib/slug.ts) ──────
def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# Endpoints to snapshot: (url, output-path-relative-to-OUT, allow_404).
# allow_404 endpoints legitimately 404 when no data exists yet (e.g. the
# researcher comparison before compare_researchers.py has ever run); those are
# written as JSON null so the static fetch still resolves.
# Note: /api/books and /api/nonfiction/books are NOT here — they're exported
# separately by _export_books() so a `slug` can be injected into each book.
SIMPLE_ENDPOINTS: list[tuple[str, str, bool]] = [
    # ── Fiction ──
    ("/api/tiers", "fiction/tiers.json", False),
    ("/api/valid-genres", "fiction/valid-genres.json", False),
    ("/api/series", "fiction/series.json", False),
    ("/api/series/tiers", "fiction/series-tiers.json", False),
    ("/api/timeline", "fiction/timeline.json", False),
    ("/api/reading/stats", "fiction/reading-stats.json", False),
    ("/api/reading/status", "fiction/reading-status.json", False),
    ("/api/read-queue", "fiction/read-queue.json", False),
    ("/api/queue", "fiction/queue.json", False),
    # ── Nonfiction ──
    ("/api/nonfiction/tiers", "nonfiction/tiers.json", False),
    ("/api/nonfiction/valid-genres", "nonfiction/valid-genres.json", False),
    ("/api/nonfiction/series", "nonfiction/series.json", False),
    ("/api/nonfiction/series/tiers", "nonfiction/series-tiers.json", False),
    ("/api/nonfiction/timeline", "nonfiction/timeline.json", False),
    ("/api/nonfiction/reading/stats", "nonfiction/reading-stats.json", False),
    ("/api/nonfiction/reading/status", "nonfiction/reading-status.json", False),
    ("/api/nonfiction/read-queue", "nonfiction/read-queue.json", False),
    ("/api/nonfiction/queue", "nonfiction/queue.json", False),
    # ── Combined / analytics / calibration ──
    ("/api/stats", "stats.json", False),
    ("/api/delta-log", "delta-log.json", False),
    ("/api/calibration/health", "calibration-health.json", False),
    ("/api/calibration/researcher-comparison", "calibration-researcher-comparison.json", True),
    # Public walk-forward track record. allow_404: absent until walkforward.py
    # has produced validation/*; the endpoint reads only committed files (never
    # runs the harness) and its payload is deterministic per commit.
    ("/api/track-record", "track-record.json", True),
]


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _lint_gate() -> None:
    """Run the deterministic data lint (scripts/lint_data.py) against books.db
    before any export work, so invalid data can never be published. ERROR-level
    findings abort loudly with fix guidance — inherited by BOTH the pre-commit
    snapshot regeneration (run_export) and the pre-push staleness gate
    (check_snapshot), which each run this exporter. WARN-level findings print but
    never block. Rules + the convention-dependent-duplicate allowlist live in
    lint_data.py / lint_allowlist.json."""
    import lint_data  # scripts/ sibling; read-only sqlite over books.db, no LLM
    result = lint_data.lint(ROOT / "books.db", lint_data.DEFAULT_ALLOWLIST)
    lint_data.print_report(result, stream=sys.stderr)
    if result["errors"]:
        print(
            "\n✗ data lint FAILED — refusing to export invalid data.\n"
            "  Fix each ERROR above via the sanctioned db_write functions\n"
            "  (set_series_number / set_done / update_book_metadata), then retry.\n"
            "  A genuinely convention-dependent duplicate can be excused in\n"
            "  scripts/lint_allowlist.json (see that file's comment).",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Environment ───────────────────────────────────────────────────────────────
_ENV_REMEDIATION = (
    "  Fix: run from the project root with the Python environment that has the\n"
    "  backend dependencies installed. Activate the project venv if you use one\n"
    "  (e.g. `source .venv/bin/activate`), or `pip install fastapi httpx` plus\n"
    "  the engine deps (numpy, openpyxl, …)."
)


def verify_env() -> int:
    """Exit 0 iff every import the export needs resolves; 1 with remediation.

    This is what the pre-push/pre-commit hooks call to fail LOUDLY when run from
    an environment that cannot produce a snapshot, rather than silently skipping
    (which would let a stale snapshot ship)."""
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
        from backend.main import app  # noqa: F401  (pulls in the full engine chain)
    except Exception as exc:  # noqa: BLE001 — any import problem must be reported
        print(
            f"ERROR: export environment not ready: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(_ENV_REMEDIATION, file=sys.stderr)
        return 1
    return 0


def _load_client():
    """Import the app + TestClient and return a client, or exit with remediation.

    Imports are done here (not at module top) so ``--verify-env`` can report a
    friendly message instead of a bare ImportError traceback at module load."""
    try:
        # Importing the app runs main.py's module-level setup, including os.chdir
        # to the project root so books.db resolves the same way it does under
        # uvicorn.
        from backend.main import app
        from fastapi.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: cannot import the backend app: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(_ENV_REMEDIATION, file=sys.stderr)
        sys.exit(1)
    # NOTE: constructed WITHOUT the `with` context manager on purpose — the
    # lifespan (engine warm-up) is not needed; endpoints build the engine
    # lazily via _get_engine(). This matches long-standing export behavior.
    return TestClient(app)


# ── Serialization (the determinism layer) ──────────────────────────────────────
def _strip_nondeterministic(obj):
    """Recursively drop generation-artifact keys so the snapshot depends only on
    books.db, never on wall-clock time. Lists keep their order (meaningful)."""
    if isinstance(obj, dict):
        return {
            k: _strip_nondeterministic(v)
            for k, v in obj.items()
            if k not in _NONDETERMINISTIC_KEYS
        }
    if isinstance(obj, list):
        return [_strip_nondeterministic(v) for v in obj]
    return obj


def _serialize(payload) -> bytes:
    """Canonical JSON bytes: keys sorted, unicode preserved, artifacts stripped."""
    clean = _strip_nondeterministic(payload)
    text = json.dumps(clean, indent=2, sort_keys=True, ensure_ascii=False)
    return text.encode("utf-8")


def _get(client, url: str, allow_404: bool):
    """GET url; return parsed JSON (or None for an allowed 404). Fails loudly."""
    res = client.get(url)
    if res.status_code == 404 and allow_404:
        return None
    if res.status_code != 200:
        _fail(f"{url} returned {res.status_code} (expected 200)")
    return res.json()


# ── Build (fetch-all, in memory — writes NOTHING) ──────────────────────────────
def build_snapshot(client) -> tuple[dict[str, bytes], dict]:
    """Fetch EVERY endpoint into memory and return ({rel_path: bytes}, stats).

    Writes nothing to disk. Fails loudly (``sys.exit``) on any non-200, slug
    collision, or count mismatch. This is the single source of the snapshot for
    both the real export and ``--check`` — so the two can never disagree."""
    files: dict[str, bytes] = {}

    # Books listings (with injected slug so the frontend never re-derives it).
    fic_data = _get(client, "/api/books", allow_404=False)
    fiction_books = fic_data.get("books", [])
    for b in fiction_books:
        b["slug"] = slugify(b["title"])
    files["fiction/books.json"] = _serialize(fic_data)

    nf_data = _get(client, "/api/nonfiction/books", allow_404=False)
    nonfiction_books = nf_data.get("books", [])
    for b in nonfiction_books:
        b["slug"] = slugify(b["title"])
    files["nonfiction/books.json"] = _serialize(nf_data)

    # Per-year fiction tiers — the tier-list page fetches ?year=YYYY for each
    # year read, so a single all-years snapshot isn't enough. Nonfiction has no
    # year_read and its endpoint ignores the param, so one file suffices there.
    years = sorted({b["year_read"] for b in fiction_books if b.get("year_read")})
    for yr in years:
        payload = _get(client, f"/api/tiers?year={yr}", allow_404=False)
        files[f"fiction/tiers-{yr}.json"] = _serialize(payload)

    # Fiction per-book scores keyed by slug (nonfiction has no scores GET
    # endpoint; its component scores are embedded in nonfiction/books.json).
    # Fail loudly on any slug collision — two titles must never share a file.
    seen: dict[str, str] = {}
    for b in fiction_books:
        title = b["title"]
        slug = slugify(title)
        if slug in seen and seen[slug] != title:
            _fail(
                "slug collision — two titles map to the same file:\n"
                f'  "{seen[slug]}"\n  "{title}"\n  -> fiction/scores/{slug}.json'
            )
        seen[slug] = title
        payload = _get(client, f"/api/books/{title}/scores", allow_404=False)
        files[f"fiction/scores/{slug}.json"] = _serialize(payload)

    # Everything else.
    for url, rel_path, allow_404 in SIMPLE_ENDPOINTS:
        files[rel_path] = _serialize(_get(client, url, allow_404))

    # Slug parity guard across every real title in both libraries (best-effort:
    # warns if node is unavailable, since export itself doesn't need node).
    all_titles = [b["title"] for b in fiction_books] + [
        b["title"] for b in nonfiction_books
    ]
    check_slug_parity(all_titles)

    # Sanity: exported score files must equal the fiction book count. Counting
    # distinct keys (not loop iterations) also catches two identical titles
    # silently collapsing into one file.
    score_files = sum(1 for k in files if k.startswith("fiction/scores/"))
    if score_files != len(fiction_books):
        _fail(
            f"score file count {score_files} != fiction book count "
            f"{len(fiction_books)} (duplicate titles?)"
        )

    stats = {
        "files": len(files),
        "fiction_books": len(fiction_books),
        "nonfiction_books": len(nonfiction_books),
        "score_files": score_files,
        "years": years,
        "total_bytes": sum(len(v) for v in files.values()),
    }
    return files, stats


def check_slug_parity(titles: list[str]) -> None:
    """Assert the TS slugify() (run via node) produces identical output to the
    Python one for every real title. Best-effort: warns (does not fail) if node
    is unavailable, since export itself doesn't need it."""
    js = r"""
const slugify = (t) =>
  t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
let data = "";
process.stdin.on("data", (c) => (data += c));
process.stdin.on("end", () => {
  const titles = JSON.parse(data);
  process.stdout.write(JSON.stringify(titles.map(slugify)));
});
"""
    try:
        proc = subprocess.run(
            ["node", "-e", js],
            input=json.dumps(titles),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  ! node unavailable — skipped TS/Python slug parity check")
        return
    if proc.returncode != 0:
        _fail(f"slug parity check failed to run node: {proc.stderr.strip()}")
    ts_slugs = json.loads(proc.stdout)
    py_slugs = [slugify(t) for t in titles]
    mismatches = [
        (t, p, j) for t, p, j in zip(titles, py_slugs, ts_slugs) if p != j
    ]
    if mismatches:
        lines = "\n".join(f'  "{t}": py="{p}" ts="{j}"' for t, p, j in mismatches)
        _fail(f"Python and TS slugify() disagree:\n{lines}")
    print(f"  ✓ slug parity: {len(titles)} titles match TS slugify()")


# ── Write (only after every response is in memory) ─────────────────────────────
def _atomic_write(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` atomically: tmp sibling + os.replace. A crash
    can never leave a half-written JSON file that later parses invalid."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def write_snapshot(files: dict[str, bytes], out_dir: Path) -> None:
    """Wipe-and-recreate ``out_dir`` and write every file atomically, then verify
    each parses back as valid JSON. Callers MUST have all bytes in memory first
    (via build_snapshot), so a failed fetch never reaches this point."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel in sorted(files):
        _atomic_write(out_dir / rel, files[rel])
    # Parse-back: every written file must be valid JSON before we report success.
    for rel in sorted(files):
        path = out_dir / rel
        try:
            with path.open(encoding="utf-8") as f:
                json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            _fail(f"written file failed JSON parse-back: {rel}: {exc}")


# ── --check (read-only staleness gate) ─────────────────────────────────────────
def _compare_trees(fresh_dir: Path, live_dir: Path) -> list[str]:
    """Return a sorted list of human-readable differences between two snapshot
    trees (byte-for-byte on file contents; also flags files present in only one
    tree). Empty list == identical."""
    def rel_files(root: Path) -> set[str]:
        if not root.exists():
            return set()
        return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

    fresh = rel_files(fresh_dir)
    live = rel_files(live_dir)
    diffs: list[str] = []
    for rel in sorted(fresh | live):
        in_fresh, in_live = rel in fresh, rel in live
        if not in_live:
            diffs.append(f"{rel}  (new — not in public/data/)")
        elif not in_fresh:
            diffs.append(f"{rel}  (stale — on disk but not in a fresh export)")
        elif (fresh_dir / rel).read_bytes() != (live_dir / rel).read_bytes():
            diffs.append(f"{rel}  (content differs)")
    return diffs


def check_snapshot(client) -> int:
    """Regenerate into a temp dir and compare to public/data/. Never touches
    public/data/. Exit 0 if byte-identical, 1 (listing diffs) otherwise."""
    _lint_gate()  # block the pre-push staleness gate on invalid data
    files, _ = build_snapshot(client)
    tmp_root = Path(tempfile.mkdtemp(prefix="rl-export-check-"))
    try:
        write_snapshot(files, tmp_root)  # also exercises atomic-write + parse-back
        diffs = _compare_trees(tmp_root, OUT)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    if diffs:
        print(
            "Snapshot is STALE — public/data/ differs from a fresh export:",
            file=sys.stderr,
        )
        for d in diffs:
            print(f"  {d}", file=sys.stderr)
        return 1
    print(f"✓ snapshot up to date ({len(files)} files, byte-identical)")
    return 0


# ── Full export ────────────────────────────────────────────────────────────────
def run_export(client) -> None:
    print("Exporting static data →", OUT)
    _lint_gate()  # block the pre-commit snapshot regeneration on invalid data
    files, stats = build_snapshot(client)
    write_snapshot(files, OUT)
    print("\nSummary")
    print(f"  files written : {stats['files']}")
    print(
        f"  fiction books : {stats['fiction_books']}  "
        f"({stats['score_files']} score files)"
    )
    print(f"  nonfiction    : {stats['nonfiction_books']} books")
    print(f"  tier years    : {stats['years'] or '—'}")
    print(f"  total size    : {stats['total_bytes'] / 1024:.1f} KiB")
    print(f"  output        : {OUT}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot the read-only API into static JSON.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Exit 0 if public/data/ is byte-identical to a fresh export, else "
        "1 (listing differing files). Never modifies public/data/.",
    )
    group.add_argument(
        "--verify-env",
        action="store_true",
        help="Exit 0 iff every import the export needs resolves; else 1 with "
        "remediation. Used by the git hooks to fail loudly on a bad environment.",
    )
    args = parser.parse_args(argv)

    if args.verify_env:
        sys.exit(verify_env())

    client = _load_client()
    if args.check:
        sys.exit(check_snapshot(client))
    run_export(client)


if __name__ == "__main__":
    main()
