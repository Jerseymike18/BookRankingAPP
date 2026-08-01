"""
goodreads_import.py
===================
Pure parser for a Goodreads "Export Library" CSV (My Books -> Import/Export).

Goodreads retired its public API (no new developer keys since Dec 2020), so the
only reliable ingest is the user-exported CSV. This module turns the raw CSV
bytes/text into normalized *staging rows*; it does NO database work and imports
no project modules, so it stays trivially unit-testable and side-effect-free.

The export is a standard CSV with a header row. The columns we use:

  Exclusive Shelf   -> shelf  (read / to-read / currently-reading)  [the router]
  Title             -> title  (a trailing "(Series, #N)" is split off)
  Author            -> author
  Number of Pages   -> words  (estimated as pages * WORDS_PER_PAGE; a heuristic,
                               refined later by the enrichment pass)
  Date Read         -> year_read + read_month  (YYYY/MM/DD; blank -> None)
  My Rating         -> goodreads_rating (1-5; 0 = unrated -> None) — a memory-jog
                       hint shown during ranking, NEVER converted into components
  My Review         -> goodreads_review (capped) — another ranking hint

`genre` and `kind` (fiction/nonfiction) are deliberately left unset here — the
export doesn't carry them reliably; the Phase-2 classify pass fills them and the
user confirms them in review. Nothing derived is computed.
"""

import csv
import io
import re

# Rough fiction words-per-page. A heuristic only — the enrichment pass refines
# `words` per book; this just gives series-length math something to chew on at
# import time when all we have is Goodreads' page count.
WORDS_PER_PAGE = 300

VALID_SHELVES = {"read", "to-read", "currently-reading"}

# Cap a stored review so a pathological export can't stage megabyte blobs.
_REVIEW_CAP = 2000

# "Title (Series Name, #N)" or "Title (Series Name #N)". The number may be a
# decimal (novella "#0.5"); the trailing "(...)" is anchored to end-of-string so
# a legitimate parenthetical like "(Illustrated)" (no "#N") is never mistaken for
# a series tag.
_SERIES_RE = re.compile(
    r"^(?P<title>.*?)\s*\((?P<series>[^()]+?)(?:,\s*|\s+)#(?P<num>\d+(?:\.\d+)?)\)\s*$")


def _norm_header(name):
    """Collapse whitespace + strip BOM and case so 'Exclusive Shelf' and
    'exclusive shelf' resolve to the same key."""
    return re.sub(r"\s+", " ", (name or "").replace("﻿", "").strip()).lower()


def _to_int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def split_series(title):
    """'The Way of Kings (The Stormlight Archive, #1)' ->
    ('The Way of Kings', 'The Stormlight Archive', 1).

    No series tag -> (title, None, None). A non-integer number (e.g. a "#0.5"
    novella) keeps the series *name* but yields series_number=None, because the
    books/recommendations schema stores an INTEGER series_number — the user sets
    the intended integer in review."""
    t = (title or "").strip()
    m = _SERIES_RE.match(t)
    if not m:
        return t, None, None
    base = m.group("title").strip()
    series = m.group("series").strip().rstrip(",").strip()
    try:
        f = float(m.group("num"))
        num = int(f) if f.is_integer() else None
    except (TypeError, ValueError):
        num = None
    return (base or t), (series or None), num


def _parse_date_read(s):
    """'YYYY/MM/DD' -> (year, month), guarded to (1900-2100, 1-12). Blank or
    unparseable -> (None, None). Tolerant of separators via digit extraction."""
    if not s:
        return None, None
    parts = re.findall(r"\d+", s)
    year = month = None
    if parts:
        y = int(parts[0])
        if 1900 <= y <= 2100:
            year = y
    if len(parts) >= 2:
        mo = int(parts[1])
        if 1 <= mo <= 12:
            month = mo
    return year, month


def parse_goodreads_csv(data):
    """Parse a Goodreads export CSV (str or bytes) -> (rows, summary).

    rows: list of dicts ready for db_write.stage_import_rows — keys: shelf, title,
    author, series, series_number, words, year_read, read_month, goodreads_rating,
    goodreads_review. (genre/kind are intentionally absent — set later.)

    summary: {total_data_rows, kept, dropped_no_title, dropped_bad_shelf,
    dropped_dupe_in_csv, by_shelf} — nothing is dropped silently; every skipped
    row is counted so the UI can report it."""
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = (data or "").lstrip("﻿")

    reader = csv.DictReader(io.StringIO(text))
    field_map = {_norm_header(h): h for h in (reader.fieldnames or [])}

    def cell(row, *names):
        for n in names:
            h = field_map.get(_norm_header(n))
            if h is not None:
                v = row.get(h)
                if v is not None and str(v).strip() != "":
                    return str(v).strip()
        return ""

    rows = []
    seen = set()
    total = dropped_no_title = dropped_bad_shelf = dropped_dupe = 0
    by_shelf = {}

    for raw in reader:
        total += 1
        shelf = cell(raw, "Exclusive Shelf").lower()
        if shelf not in VALID_SHELVES:
            # Very old exports lack "Exclusive Shelf"; fall back to Bookshelves.
            bs = cell(raw, "Bookshelves").lower()
            shelf = next((s for s in VALID_SHELVES if s in bs), "")
        if shelf not in VALID_SHELVES:
            dropped_bad_shelf += 1
            continue

        title, series, series_number = split_series(cell(raw, "Title"))
        if not title:
            dropped_no_title += 1
            continue

        author = cell(raw, "Author") or None
        key = (title.lower(), (author or "").lower())
        if key in seen:
            dropped_dupe += 1
            continue
        seen.add(key)

        pages = _to_int(cell(raw, "Number of Pages"))
        words = pages * WORDS_PER_PAGE if pages else None

        year_read, read_month = _parse_date_read(cell(raw, "Date Read"))

        rating = _to_int(cell(raw, "My Rating"))
        goodreads_rating = rating if rating else None  # 0 == unrated

        review = cell(raw, "My Review") or None
        if review:
            review = review[:_REVIEW_CAP]

        rows.append({
            "shelf": shelf,
            "title": title,
            "author": author,
            "series": series,
            "series_number": series_number,
            "words": words,
            "year_read": year_read,
            "read_month": read_month,
            "goodreads_rating": goodreads_rating,
            "goodreads_review": review,
        })
        by_shelf[shelf] = by_shelf.get(shelf, 0) + 1

    summary = {
        "total_data_rows": total,
        "kept": len(rows),
        "dropped_no_title": dropped_no_title,
        "dropped_bad_shelf": dropped_bad_shelf,
        "dropped_dupe_in_csv": dropped_dupe,
        "by_shelf": by_shelf,
    }
    return rows, summary
