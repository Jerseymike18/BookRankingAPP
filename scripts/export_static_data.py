#!/usr/bin/env python3
"""Snapshot the read-only API into static JSON for a hosted, backend-free deploy.

Runs the existing FastAPI app in-process (no uvicorn) via ``TestClient`` and
writes every read-only GET endpoint's response to ``frontend/public/data/``.
The frontend, built with ``NEXT_PUBLIC_STATIC_DATA=1``, reads those files
instead of calling ``localhost:8000`` — see ``frontend/lib/api.ts``.

Local dev is untouched: this only writes files; it never mutates ``books.db``
and never touches any endpoint that writes state or spends Anthropic tokens
(predict/research, discover, lookup, queue writes, recommendation meta, LOO).

Usage (from the project root or frontend/):
    python3 scripts/export_static_data.py
    npm run export-data          # from frontend/

Dependencies: stdlib + the backend's own deps. ``TestClient`` needs ``httpx``,
which FastAPI already pulls in — no extra install for this repo.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing the app runs main.py's module-level setup, including os.chdir to the
# project root so books.db resolves the same way it does under uvicorn.
from backend.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OUT = ROOT / "frontend" / "public" / "data"


# ── Slug rule (MUST stay identical to slugify() in frontend/lib/slug.ts) ──────
def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# Endpoints to snapshot: (url, output-path-relative-to-OUT, allow_404).
# allow_404 endpoints legitimately 404 when no data exists yet (e.g. the
# researcher comparison before compare_researchers.py has ever run); those are
# written as JSON null so the static fetch still resolves.
# Note: /api/books and /api/nonfiction/books are NOT here — they're exported
# separately by export_books() so a `slug` can be injected into each book.
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
]


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _write(rel_path: str, payload) -> int:
    """Write pretty JSON under OUT/rel_path; return bytes written."""
    dest = OUT / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    dest.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _get(client: TestClient, url: str, allow_404: bool):
    """GET url; return parsed JSON (or None for an allowed 404). Fails loudly."""
    res = client.get(url)
    if res.status_code == 404 and allow_404:
        return None
    if res.status_code != 200:
        _fail(f"{url} returned {res.status_code} (expected 200)")
    return res.json()


def export_books(client: TestClient, url: str, rel_path: str) -> tuple[list, int]:
    """Snapshot a books listing, injecting a `slug` into every book so the
    frontend never re-derives it. Returns (books, bytes_written)."""
    data = _get(client, url, allow_404=False)
    books = data.get("books", [])
    for b in books:
        b["slug"] = slugify(b["title"])
    total = _write(rel_path, data)
    return books, total


def export_scores(client: TestClient, books: list, out_dir: str) -> tuple[int, int]:
    """Snapshot per-book /scores for each fiction book, keyed by slug. Returns
    (files_written, bytes_written). Fails loudly on any slug collision."""
    seen: dict[str, str] = {}
    files = 0
    total = 0
    for b in books:
        title = b["title"]
        slug = slugify(title)
        if slug in seen and seen[slug] != title:
            _fail(
                "slug collision — two titles map to the same file:\n"
                f'  "{seen[slug]}"\n  "{title}"\n  -> {out_dir}/{slug}.json'
            )
        seen[slug] = title
        payload = _get(client, f"/api/books/{title}/scores", allow_404=False)
        total += _write(f"{out_dir}/{slug}.json", payload)
        files += 1
    return files, total


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


def main() -> None:
    # Stale-file safety: nuke and recreate the whole data tree each run.
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    client = TestClient(app)
    total_bytes = 0
    files_written = 0

    print("Exporting static data →", OUT)

    # Books listings (with injected slug) + per-book fiction scores.
    fiction_books, n = export_books(client, "/api/books", "fiction/books.json")
    total_bytes += n
    files_written += 1

    nonfiction_books, n = export_books(
        client, "/api/nonfiction/books", "nonfiction/books.json"
    )
    total_bytes += n
    files_written += 1

    # Per-year fiction tiers — the tier-list page fetches ?year=YYYY for each
    # year read, so a single all-years snapshot isn't enough. Nonfiction has no
    # year_read and its endpoint ignores the param, so one file suffices there.
    years = sorted({b["year_read"] for b in fiction_books if b.get("year_read")})
    for yr in years:
        payload = _get(client, f"/api/tiers?year={yr}", allow_404=False)
        total_bytes += _write(f"fiction/tiers-{yr}.json", payload)
        files_written += 1

    # Fiction per-book scores (nonfiction has no scores GET endpoint; its
    # component scores are already embedded in nonfiction/books.json).
    score_files, n = export_scores(client, fiction_books, "fiction/scores")
    total_bytes += n
    files_written += score_files
    print(f"  ! nonfiction has no per-book scores endpoint — skipped (scores are embedded in books.json)")

    # Everything else.
    for url, rel_path, allow_404 in SIMPLE_ENDPOINTS:
        payload = _get(client, url, allow_404)
        total_bytes += _write(rel_path, payload)
        files_written += 1

    # Slug parity guard across every real title in both libraries.
    all_titles = [b["title"] for b in fiction_books] + [
        b["title"] for b in nonfiction_books
    ]
    check_slug_parity(all_titles)

    # Sanity: exported score files must equal the fiction book count.
    if score_files != len(fiction_books):
        _fail(
            f"score file count {score_files} != fiction book count {len(fiction_books)}"
        )

    print("\nSummary")
    print(f"  files written : {files_written}")
    print(f"  fiction books : {len(fiction_books)}  ({score_files} score files)")
    print(f"  nonfiction    : {len(nonfiction_books)} books")
    print(f"  tier years    : {years or '—'}")
    print(f"  total size    : {total_bytes / 1024:.1f} KiB")
    print(f"  output        : {OUT}")


if __name__ == "__main__":
    main()
