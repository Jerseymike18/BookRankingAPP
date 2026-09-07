"""
shelf_export.py
===============
Pure builder for a Goodreads-shaped library CSV — the one file that BOTH
Goodreads and The StoryGraph accept on import. The exact mirror of
`goodreads_import.py`, and deliberately its inverse.

ONE FORMAT, NOT TWO
-------------------
StoryGraph documents its manual import as a CSV "using the same formatting as
Goodreads' CSV export", with the column names used exactly; Goodreads' own
importer is built to read its own export. So there is one wire format here.
Writing a second, StoryGraph-native file would be a second thing to keep
correct for no gain, and StoryGraph's native export carries columns (moods,
pace, content warnings) the Ledger has no data for and must not invent.

Stdlib only, plus ONE pure sibling — `goodreads_import`, for `split_series`.
That import is the point rather than a compromise: `join_series` here is that
function's inverse, and sharing the regex is what makes the round trip
(build -> to_csv -> parse_goodreads_csv) provably lossless instead of
coincidentally lossless. No DB work, no project state, no side effects.

THE STAR MAP IS A LINEAR HALVING, AND THAT IS A DECISION
--------------------------------------------------------
    stars = clamp(floor(score / 2 + 0.5), 1, 5)

The Ledger's score is 0-10; a star is the same scale at a fifth of the
resolution. Two properties chose this over a tier/percentile map (owner
decision, 2026-09-03):

  * It is ABSOLUTE. Five stars means score >= 9, always, for every reader. A
    tier map would call a 7.5 book five stars for being top-9% of a small
    library — a claim about the library, not about the book, landing in a field
    the receiving site presents as a claim about the book.
  * It is STABLE. Adding a book cannot change an already-exported star. Tier
    bands are percentiles, so they re-cut on every add: export twice a month
    apart and the far site would be handed two different ratings for one book,
    with nothing to say which was meant.

It IS lossy, by a factor of five. That is disclosed rather than worked around —
the exact score rides along in Private Notes (a Goodreads field that is private
to the reader), so nothing the Ledger knows is silently discarded.

HALF-UP, NOT `round()`. Python's `round` is banker's rounding: `round(2.5)` is
2 while `round(3.5)` is 4, so a score of 5.0 would land on 2 stars and 7.0 on 4.
`floor(x + 0.5)` is the rule a reader can check by hand.

MISSING IS NOT ONE STAR
-----------------------
A book with no score exports an EMPTY `My Rating`, never a 1. Both sites read
blank/0 as "unrated", which is the true statement; a 1 would be a rating nobody
gave. The clamp's FLOOR is for the opposite case — a real score below 1.0 would
round to 0 and be read as unrated — so it lifts to the lowest value that is
actually a rating. The clamp's ceiling never fires on a 0-10 input; it is there
so a caller passing a wider scale cannot emit a 6.

PREDICTED SCORES ARE NEVER STARS
--------------------------------
`to-read` rows go out with `My Rating` blank. The Ledger holds a predicted score
for every one of them, and exporting it would put an ESTIMATE into a field that
Goodreads and StoryGraph both present as a rating the reader gave. Same rule as
the served conformal interval and the omitted-rather-than-invented band: an
estimate must never be able to pass as a measurement. `build_rows` cannot be
talked out of this — it ignores `score` on the to-read side rather than trusting
callers to pass None.

DATES: THE DAY IS FILLED IN, AND THE UI SAYS SO
-----------------------------------------------
The Ledger tracks year + month read; both sites want YYYY/MM/DD. A blank date
would lose the read year entirely (and with it the yearly stats that are the
reason anyone migrates a library), and neither importer documents accepting a
partial date. So the day is set to the 1st, the month to January when only a
year is known, and the download UI states both substitutions in words. Filling
a field silently is the thing to avoid; filling it and saying so is not.

WHAT IS DELIBERATELY LEFT BLANK
-------------------------------
  * `ISBN` / `ISBN13` — the Ledger stores none. Both sites then match on
    title+author, which is why the series tag is re-attached to the title.
  * `Number of Pages` — `words` is often itself an estimate (the importer
    derives it as pages x 300), so emitting a page count would be a guess
    reconstructed from a guess, into a field that seeds a new book record.
  * `Date Added` — unknown, and an invented one would sort a reader's whole
    library wrong on arrival.
  * `Author l-f` — "Last, First" is a guess for any name that is not
    Western-ordered, and both importers read `Author`.
  * `My Review` — a review is public on Goodreads. The Ledger's component
    scores are not a review, and publishing something the reader never wrote
    under their name is not ours to do.
"""

import csv
import io
import math

import goodreads_import

# The Goodreads "Export Library" header, verbatim and in order. Emitted in full
# even where every value is blank: StoryGraph's docs say to use the column names
# exactly, and a header that matches an untouched export is the one shape both
# importers are known to accept. Widening a subset later is a guess; this is not.
GOODREADS_COLUMNS = (
    "Book Id", "Title", "Author", "Author l-f", "Additional Authors",
    "ISBN", "ISBN13", "My Rating", "Average Rating", "Publisher", "Binding",
    "Number of Pages", "Year Published", "Original Publication Year",
    "Date Read", "Date Added", "Bookshelves", "Bookshelves with positions",
    "Exclusive Shelf", "My Review", "Spoiler", "Private Notes", "Read Count",
    "Owned Copies",
)

# The exclusive shelves this module emits. "currently-reading" and
# "did-not-finish" are valid on both sites but the Ledger has no honest source
# for them here, so they are not written rather than approximated.
SHELF_READ = "read"
SHELF_TO_READ = "to-read"

MIN_STARS = 1
MAX_STARS = 5


def stars_for(score):
    """0-10 Ledger score -> 1-5 stars, or None for "no rating".

    Half-up (`floor(x + 0.5)`), NOT `round()` — see the module docstring; the
    banker's rounding in `round` would put 5.0 on 2 stars and 7.0 on 4.
    A None/NaN/non-numeric score returns None, which the writer renders as an
    empty cell: missing is not one star."""
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s != s:                      # NaN — a missing component, not a zero
        return None
    stars = int(math.floor(s / 2.0 + 0.5))
    return max(MIN_STARS, min(MAX_STARS, stars))


def join_series(title, series, series_number):
    """'The Way of Kings' + ('The Stormlight Archive', 1) ->
    'The Way of Kings (The Stormlight Archive, #1)'.

    The inverse of `goodreads_import.split_series`, and re-attaching the tag is
    not cosmetic: with no ISBN to match on, both sites fall back to title+author,
    and Goodreads' own export writes titles this way — so the tagged form is the
    one their matcher was built against.

    No series, or a series with no ordinal, returns the bare title: Goodreads'
    tag syntax carries a number, and '(The Stormlight Archive)' would parse back
    as part of the title. A title that ALREADY carries a tag is returned
    unchanged (checked with `split_series` itself, so the two can never disagree
    about what a tag looks like) rather than gaining a second one."""
    t = (title or "").strip()
    s = (series or "").strip()
    if not t or not s or series_number is None:
        return t
    if goodreads_import.split_series(t)[1] is not None:
        return t
    try:
        n = float(series_number)
    except (TypeError, ValueError):
        return t
    num = str(int(n)) if n.is_integer() else str(n)
    return f"{t} ({s}, #{num})"


def format_date_read(year_read, read_month):
    """(year, month) -> 'YYYY/MM/DD', with the day filled in as 01.

    No year -> '' (nothing to state). Year but no month -> January, because a
    partial date is not documented as accepted by either importer and a dropped
    date loses the read year. Both substitutions are stated in the download UI —
    see the module docstring. An out-of-range month is treated as absent rather
    than clamped: a 13 is a bug upstream, and January is at least a value the
    reader can recognise as the fallback."""
    if year_read is None:
        return ""
    try:
        y = int(year_read)
    except (TypeError, ValueError):
        return ""
    if not (1900 <= y <= 2100):
        return ""
    m = 1
    try:
        mm = int(read_month)
        if 1 <= mm <= 12:
            m = mm
    except (TypeError, ValueError):
        pass
    return f"{y:04d}/{m:02d}/01"


def _private_note(score, score_label, rank):
    """The exact Ledger score, preserved in Goodreads' PRIVATE Notes field.

    The star map throws away four fifths of the resolution; this is where that
    resolution goes instead of nowhere. Private, not `My Review`: it is a number
    the reader's own instrument produced, not a review they wrote, and it should
    not appear under their name on a public book page.

    `score_label` names WHICH number it is — fiction ranks by the genre-weighted
    WA, nonfiction by Total Average — because a bare '8.94' in a foreign app a
    year from now is not self-explanatory. Empty when there is no score."""
    if score is None:
        return ""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return ""
    if s != s:
        return ""
    note = f"The Reading Ledger — {score_label} {s:.2f}/10"
    if rank is not None:
        try:
            note += f", rank #{int(rank)}"
        except (TypeError, ValueError):
            pass
    return note


def _row(shelf, book, score_label, rated):
    """One CSV row as a {column: value} dict. Every column in GOODREADS_COLUMNS
    is present; the ones with no honest source stay empty (docstring says which
    and why). `rated` is False for to-read, and it gates BOTH the star and the
    private note — an unread book's stored score is a prediction, and a note
    reading 'WA 8.1' beside a blank rating invites exactly the misreading the
    blank rating exists to prevent."""
    score = book.get("score") if rated else None
    stars = stars_for(score) if rated else None
    row = {c: "" for c in GOODREADS_COLUMNS}
    row["Title"] = join_series(book.get("title"), book.get("series"),
                               book.get("series_number"))
    row["Author"] = (book.get("author") or "").strip()
    row["My Rating"] = "" if stars is None else str(stars)
    row["Date Read"] = (format_date_read(book.get("year_read"),
                                         book.get("read_month"))
                        if rated else "")
    row["Exclusive Shelf"] = shelf
    # Goodreads' export repeats the exclusive shelf here; StoryGraph maps any
    # shelf it does not recognise to a tag. Mirroring the exclusive shelf and
    # nothing else means the import creates no shelf or tag the reader did not
    # already have — genres would arrive as invented shelves on their account.
    row["Bookshelves"] = shelf
    row["Private Notes"] = (_private_note(score, score_label, book.get("rank"))
                            if rated else "")
    return row


def build_rows(read=(), to_read=(), score_label="WA"):
    """Ledger books -> CSV row dicts, ready for `to_csv`.

    Each input item is a dict with: title, author, series, series_number,
    score, rank, year_read, read_month (all optional but `title`).

    `read` rows carry a star; `to_read` rows never do — the second argument is a
    different SHELF and a different claim, not the same list with a flag. A book
    with no title is dropped (there is nothing for either site to match on);
    everything else is counted by `summarise`, never dropped silently.

    `score_label` names the scale for the private note: fiction's WA and
    nonfiction's Total Average are different numbers and the export says which."""
    rows = []
    for b in read:
        if (b.get("title") or "").strip():
            rows.append(_row(SHELF_READ, b, score_label, rated=True))
    for b in to_read:
        if (b.get("title") or "").strip():
            rows.append(_row(SHELF_TO_READ, b, score_label, rated=False))
    return rows


def dedupe(rows):
    """Drop rows repeating a (title, author) already seen -> (rows, dropped).

    Both sites match on title+author, so a duplicate does not create a second
    entry — it re-writes the first, and the LAST one wins. Fiction and
    nonfiction are separate tables here and can hold the same title, and a book
    can sit in a library and a to-read queue at once; without this the export
    order would quietly decide which claim survived. First occurrence wins,
    which keeps `read` ahead of `to-read` given the caller's ordering.

    Mirrors `goodreads_import`'s `dropped_dupe_in_csv`: counted, not hidden."""
    out, seen, dropped = [], set(), 0
    for r in rows:
        key = (r["Title"].strip().lower(), r["Author"].strip().lower())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(r)
    return out, dropped


def summarise(rows, dropped_duplicate=0, skipped_in_progress=0):
    """What the file actually contains, for the UI to state before download.

    `unrated_read` is the one worth surfacing: a book on the `read` shelf with
    no star, because the Ledger had no score for it. It is not an error and not
    a dropped row — but a reader who expected every read book to arrive rated
    should be told the count rather than discover it on the far side."""
    read = sum(1 for r in rows if r["Exclusive Shelf"] == SHELF_READ)
    to_read = sum(1 for r in rows if r["Exclusive Shelf"] == SHELF_TO_READ)
    unrated = sum(1 for r in rows
                  if r["Exclusive Shelf"] == SHELF_READ and not r["My Rating"])
    return {
        "total": len(rows),
        "read": read,
        "to_read": to_read,
        "unrated_read": unrated,
        "dropped_duplicate": dropped_duplicate,
        "skipped_in_progress": skipped_in_progress,
    }


def to_csv(rows):
    """Row dicts -> CSV text with the Goodreads export header.

    CRLF line endings and minimal quoting (csv's defaults) — RFC 4180, and what
    an untouched Goodreads export looks like. No BOM: `parse_goodreads_csv`
    strips one if present but neither site's importer needs it, and a BOM is one
    more thing a naive downstream parser can trip on."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(GOODREADS_COLUMNS),
                       extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
