#!/usr/bin/env python3
"""
scripts/lint_data.py — deterministic, zero-LLM data lint for books.db.

Read-only: plain sqlite3 SELECTs only (reads are unrestricted, so no engine
import is needed). It exists so the class of silent data errors that used to
ship — duplicate series positions, read books not marked done, bad genres —
can never reach the published snapshot again.

Two severities:
  * ERROR — a real data bug. Nonzero exit; blocks the export (and therefore the
    pre-commit snapshot regeneration and the pre-push staleness gate, both of
    which run the exporter).
  * WARN  — worth a look but not a bug (or a deliberate library convention like
    an intentionally-unnumbered series). Printed; never blocks.

Wired into scripts/export_static_data.py (see _lint_gate there), so it runs on
every publish. Also runnable standalone:

    python3 scripts/lint_data.py            # human report, exit 1 iff any ERROR
    python3 scripts/lint_data.py --json     # machine-readable
    python3 scripts/lint_data.py --db copy.db --allowlist scripts/lint_allowlist.json

Scope is the FICTION tables (books, recommendations) — the pair the snapshot and
the series/dup machinery are built on. Nonfiction is deliberately out of scope.

ALLOWLIST: scripts/lint_allowlist.json excuses specific duplicate-series-position
groups that are genuinely convention-dependent (owner decision pending), so a
push isn't blocked while they're being decided. Allowlisted groups still surface
as WARN with their reason. Remove an entry to turn it back into a blocking ERROR.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "books.db"
DEFAULT_ALLOWLIST = Path(__file__).resolve().parent / "lint_allowlist.json"

# A series whose NAME contains this token is an intentionally-unnumbered grouping
# (e.g. 'Standalone', 'Discworld: Standalone'), never a numbered sequence — so a
# NULL series_number on it is by-design, not a gap.
STANDALONE_TOKEN = "standalone"

Finding = namedtuple("Finding", "level table title message")


# ── helpers ────────────────────────────────────────────────────────────────────
def _norm_num(n):
    """Normalise a series_number to int when whole (2.0 -> 2) so it compares and
    prints the same whether sqlite hands back an int or a float, and matches the
    integers written in the JSON allowlist."""
    if n is None:
        return None
    f = float(n)
    return int(f) if f == int(f) else f


def load_allowlist(path: Path) -> dict:
    """Return {(table, series, norm_number): reason} for excused duplicate groups.
    A missing file is fine (empty allowlist). A malformed file is a HARD error —
    a silently-ignored allowlist could hide the very bugs this lint exists to
    catch, or wrongly unblock a push."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        out = {}
        for e in raw.get("duplicate_series_position", []):
            key = (e["table"], e["series"], _norm_num(e["series_number"]))
            out[key] = e.get("reason", "(no reason given)")
        return out
    except (ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: cannot parse allowlist {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _valid_genres(con) -> set:
    """Mirror db_write._valid_genres — the exact fiction-genre source of truth."""
    return {r[0] for r in con.execute("SELECT genre FROM genre_weights")}


# ── checks ──────────────────────────────────────────────────────────────────────
FICTION_TABLES = ("books", "recommendations")


def _check_duplicate_positions(con, allow, errors, warns):
    """ERROR: two rows share the same (series, series_number) in one table.
    An allowlisted group is downgraded to WARN (visible, non-blocking)."""
    for tbl in FICTION_TABLES:
        rows = con.execute(
            f"SELECT series, series_number, COUNT(*) n, GROUP_CONCAT(title, ' / ') "
            f"FROM {tbl} WHERE series IS NOT NULL AND series_number IS NOT NULL "
            f"GROUP BY series, series_number HAVING COUNT(*) > 1 "
            f"ORDER BY series, series_number"
        ).fetchall()
        for series, num, n, titles in rows:
            key = (tbl, series, _norm_num(num))
            msg = f"duplicate series position: {series} #{_norm_num(num)} ({n} rows)"
            if key in allow:
                warns.append(Finding("WARN", tbl, titles,
                                     f"[allowlisted] {msg} — {allow[key]}"))
            else:
                errors.append(Finding("ERROR", tbl, titles,
                                      f"{msg}; fix one via set_series_number()"))


def _check_read_not_done(con, errors):
    """ERROR: a title in books (i.e. read/rated) whose recommendations row still
    reads done=0. A read book must be marked done on its prediction record."""
    for (title,) in con.execute(
        "SELECT b.title FROM books b JOIN recommendations r ON b.title = r.title "
        "WHERE r.done = 0 ORDER BY b.title"
    ):
        errors.append(Finding("ERROR", "recommendations", title,
                              "read book (present in books) but recommendation "
                              "row has done=0; run set_done(title, True)"))


def _check_required_fields(con, errors):
    """ERROR: missing identity fields, or a genre outside genre_weights. Applied
    to both fiction tables (db_write validates them identically)."""
    valid = _valid_genres(con)
    for tbl in FICTION_TABLES:
        for (rid,) in con.execute(
            f"SELECT id FROM {tbl} WHERE title IS NULL OR TRIM(title) = ''"
        ):
            errors.append(Finding("ERROR", tbl, f"<row id {rid}>",
                                  "title is NULL/empty"))
        for (title,) in con.execute(
            f"SELECT title FROM {tbl} WHERE author IS NULL OR TRIM(author) = ''"
        ):
            errors.append(Finding("ERROR", tbl, title, "author is NULL/empty"))
        for (title,) in con.execute(
            f"SELECT title FROM {tbl} WHERE genre IS NULL OR TRIM(genre) = ''"
        ):
            errors.append(Finding("ERROR", tbl, title, "genre is NULL/empty"))
        for title, genre in con.execute(
            f"SELECT title, genre FROM {tbl} "
            f"WHERE genre IS NOT NULL AND TRIM(genre) <> ''"
        ):
            if genre not in valid:
                errors.append(Finding("ERROR", tbl, title,
                                      f"genre '{genre}' is not in genre_weights"))


def _check_partial_numbering(con, warns):
    """WARN: a series that IS numbered (>=1 member has a series_number) but has a
    member with NULL series_number — a likely missing number. Fully-unnumbered
    series (Drenai Saga, Hainish Cycle) and standalone-marker groupings are a
    deliberate library convention and are NOT flagged."""
    for tbl in FICTION_TABLES:
        for title, series in con.execute(
            f"SELECT title, series FROM {tbl} t "
            f"WHERE series IS NOT NULL AND series_number IS NULL "
            f"  AND LOWER(series) NOT LIKE '%{STANDALONE_TOKEN}%' "
            f"  AND EXISTS (SELECT 1 FROM {tbl} x "
            f"              WHERE x.series = t.series AND x.series_number IS NOT NULL) "
            f"ORDER BY series, title"
        ):
            warns.append(Finding("WARN", tbl, title,
                                 f"series '{series}' is numbered elsewhere but this "
                                 f"entry has no series_number (possible missing number)"))


def _check_word_counts(con, warns):
    """WARN: a rated book with no word count (needed by word-count-aware views)."""
    for (title,) in con.execute(
        "SELECT title FROM books WHERE words IS NULL OR words = 0 ORDER BY title"
    ):
        warns.append(Finding("WARN", "books", title, "words is NULL or 0"))


def _check_whitespace(con, warns):
    """WARN: leading/trailing or doubled whitespace in title/author."""
    for tbl in FICTION_TABLES:
        for title, author in con.execute(f"SELECT title, author FROM {tbl}"):
            for field, val in (("title", title), ("author", author)):
                if val is None:
                    continue
                if val != val.strip():
                    warns.append(Finding("WARN", tbl, val,
                                         f"{field} has leading/trailing whitespace"))
                elif "  " in val:
                    warns.append(Finding("WARN", tbl, val,
                                         f"{field} has a doubled space"))


def _check_author_spellings(con, warns):
    """WARN: one author under two near-identical spellings — casefold+strip is
    equal but the raw text differs (cheap canonicalization only; no fuzzy match).
    Union of both fiction tables."""
    raw = [r[0] for r in con.execute(
        "SELECT author FROM books WHERE author IS NOT NULL "
        "UNION SELECT author FROM recommendations WHERE author IS NOT NULL"
    )]
    groups: dict[str, set] = {}
    for a in raw:
        groups.setdefault(a.strip().casefold(), set()).add(a)
    for key, variants in sorted(groups.items()):
        if len(variants) > 1:
            warns.append(Finding("WARN", "books+recs", " / ".join(sorted(variants)),
                                 "author appears under near-identical spellings"))


# ── driver ──────────────────────────────────────────────────────────────────────
def lint(db_path=DEFAULT_DB, allowlist_path=DEFAULT_ALLOWLIST) -> dict:
    """Run every check against db_path. Returns {'errors': [...], 'warns': [...]}
    of Finding tuples. Read-only; opens the DB in a way that never writes."""
    allow = load_allowlist(allowlist_path)
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    errors: list[Finding] = []
    warns: list[Finding] = []
    try:
        _check_duplicate_positions(con, allow, errors, warns)
        _check_read_not_done(con, errors)
        _check_required_fields(con, errors)
        _check_partial_numbering(con, warns)
        _check_word_counts(con, warns)
        _check_whitespace(con, warns)
        _check_author_spellings(con, warns)
    finally:
        con.close()
    return {"errors": errors, "warns": warns}


def format_lines(result: dict) -> list[str]:
    lines = []
    for f in result["errors"] + result["warns"]:
        lines.append(f"{f.level:5} | {f.table} | {f.title} | {f.message}")
    return lines


def print_report(result: dict, stream=sys.stdout) -> None:
    for line in format_lines(result):
        print(line, file=stream)
    ne, nw = len(result["errors"]), len(result["warns"])
    print(f"data lint: {ne} error(s), {nw} warning(s).", file=stream)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic data lint for books.db.")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="path to the SQLite DB")
    ap.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST),
                    help="path to lint_allowlist.json")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    result = lint(args.db, args.allowlist)
    if args.json:
        print(json.dumps({
            "errors": [f._asdict() for f in result["errors"]],
            "warns": [f._asdict() for f in result["warns"]],
            "summary": {"errors": len(result["errors"]),
                        "warnings": len(result["warns"])},
        }, indent=2, ensure_ascii=False))
    else:
        print_report(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
