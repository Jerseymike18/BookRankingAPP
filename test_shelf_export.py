"""
test_shelf_export.py — the library EXIT door: stars, shelves, and the round trip
================================================================================
`shelf_export.py` writes the Goodreads-shaped CSV that both Goodreads and The
StoryGraph accept on import. It is pure (stdlib + one pure sibling), so this
whole file runs offline with zero API spend and zero DB access.

It is weighted toward the two ways this can LIE:

  * a star that is not the score it claims to be — banker's rounding silently
    demoting a 5.0, a missing score arriving as one star, a predicted score
    reaching a rating field;
  * a row that says something the reader never claimed — an unfinished book on
    the `read` shelf, a duplicate quietly overwriting the rated copy, a title
    that no longer matches the book it names.

The round-trip checks are the strongest ones here: whatever `build_rows` emits
is fed straight back through `goodreads_import.parse_goodreads_csv`, the module
that reads real Goodreads exports. If the two ever disagree about what a series
tag or a shelf looks like, that is the failure the far site would have hit.

Run:  python3 test_shelf_export.py     (exit 0 = pass, 1 = fail)
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import shelf_export as se
import goodreads_import as gi

_results = []


def check(name, condition, detail=""):
    _results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


# ─────────────────────────── the star map ───────────────────────────────────
def test_stars():
    print("\nStar map — linear halving, half-up, clamped 1-5")

    # The documented anchor: five stars means score >= 9, always.
    check("9.00 -> 5", se.stars_for(9.00) == 5, str(se.stars_for(9.00)))
    check("8.99 -> 4", se.stars_for(8.99) == 4, str(se.stars_for(8.99)))
    check("10.0 -> 5", se.stars_for(10.0) == 5)

    # The live library's real span, and the figures shown to the owner when this
    # map was chosen. A change here changes what every reader's export says.
    for score, want in ((9.70, 5), (8.90, 4), (7.20, 4), (6.10, 3),
                        (4.90, 2), (3.78, 2)):
        check(f"{score:.2f} -> {want}", se.stars_for(score) == want,
              str(se.stars_for(score)))

    # REGRESSION: Python's round() is banker's rounding, so round(5.0/2) is 2
    # while round(7.0/2) is 4 — a score of 5.0 would export a star lower than the
    # rule states. floor(x + 0.5) is the rule; this is the check that it is used.
    check("5.0 -> 3 (not 2: banker's rounding guard)", se.stars_for(5.0) == 3,
          str(se.stars_for(5.0)))
    check("7.0 -> 4", se.stars_for(7.0) == 4)
    check("3.0 -> 2 (not 2 by luck: 1.5 rounds UP)", se.stars_for(3.0) == 2)

    # Missing is not one star — blank means unrated, which is the true statement.
    check("None -> None", se.stars_for(None) is None)
    check("NaN -> None", se.stars_for(float("nan")) is None)
    check("'' -> None", se.stars_for("") is None)
    check("non-numeric -> None", se.stars_for("eight") is None)

    # The floor exists so a REAL low score is not mistaken for "unrated"; the
    # ceiling so a caller on a wider scale cannot emit a 6.
    check("0.4 -> 1 (a real score, not unrated)", se.stars_for(0.4) == 1)
    check("negative -> 1", se.stars_for(-2.0) == 1)
    check("100 -> 5", se.stars_for(100) == 5)

    # Monotone across the whole domain — a higher score can never earn less.
    seq = [se.stars_for(i / 100.0) for i in range(0, 1001)]
    check("monotone non-decreasing over 0.00-10.00",
          all(a <= b for a, b in zip(seq, seq[1:])))
    check("only ever 1-5", set(seq) == {1, 2, 3, 4, 5}, str(sorted(set(seq))))


# ───────────────────────── series tag / dates ───────────────────────────────
def test_join_series():
    print("\njoin_series — the inverse of goodreads_import.split_series")

    t = se.join_series("The Way of Kings", "The Stormlight Archive", 1)
    check("tag is appended", t == "The Way of Kings (The Stormlight Archive, #1)", t)
    check("split_series inverts it",
          gi.split_series(t) == ("The Way of Kings", "The Stormlight Archive", 1),
          str(gi.split_series(t)))

    f = se.join_series("Edgedancer", "The Stormlight Archive", 2.5)
    check("fractional ordinal survives", f == "Edgedancer (The Stormlight Archive, #2.5)", f)
    check("fractional inverts", gi.split_series(f)[2] == 2.5, str(gi.split_series(f)))

    # No ordinal -> no tag: Goodreads' syntax carries a number, and a bare
    # "(Series)" parses back as part of the title on the way in.
    check("series with no number -> bare title",
          se.join_series("Piranesi", "Some Series", None) == "Piranesi")
    check("no series -> bare title", se.join_series("Piranesi", "", 1) == "Piranesi")
    check("no title -> ''", se.join_series("", "S", 1) == "")

    # An already-tagged title must not gain a second tag.
    already = "Deadhouse Gates (Malazan, #2)"
    check("already-tagged title is untouched",
          se.join_series(already, "Malazan", 2) == already,
          se.join_series(already, "Malazan", 2))


def test_dates():
    print("\nformat_date_read — the day is filled in, the year is never lost")
    check("year+month -> YYYY/MM/01", se.format_date_read(2026, 3) == "2026/03/01",
          se.format_date_read(2026, 3))
    check("year only -> January 1st", se.format_date_read(2025, None) == "2025/01/01",
          se.format_date_read(2025, None))
    check("no year -> ''", se.format_date_read(None, 6) == "")
    check("out-of-range month falls back to January",
          se.format_date_read(2025, 13) == "2025/01/01")
    check("absurd year -> '' (not a fabricated date)",
          se.format_date_read(12, 1) == "")
    # Whatever is written must be readable by the importer that reads real exports.
    check("parses back through goodreads_import",
          gi._parse_date_read(se.format_date_read(2026, 3)) == (2026, 3),
          str(gi._parse_date_read(se.format_date_read(2026, 3))))


# ───────────────────────────── row shape ────────────────────────────────────
READ = [{"title": "The Crippled God", "author": "Steven Erikson",
         "series": "Malazan: Book of the Fallen", "series_number": 10,
         "score": 9.42, "rank": 3, "year_read": 2026, "read_month": 5},
        {"title": "Unscored Book", "author": "A. Nother",
         "score": None, "rank": None, "year_read": 2024, "read_month": None}]

TBR = [{"title": "Piranesi", "author": "Susanna Clarke",
        "series": None, "series_number": None,
        # A caller passing a predicted score must not be able to publish it.
        "score": 8.10, "rank": 12}]


def test_rows():
    print("\nbuild_rows — shelves, ratings, and what must never be written")
    rows = se.build_rows(read=READ, to_read=TBR)
    by_title = {r["Title"]: r for r in rows}

    r = by_title["The Crippled God (Malazan: Book of the Fallen, #10)"]
    check("read row is on the read shelf", r["Exclusive Shelf"] == "read")
    check("read row carries its star", r["My Rating"] == "5", r["My Rating"])
    check("read row carries its date", r["Date Read"] == "2026/05/01", r["Date Read"])
    check("Bookshelves mirrors the exclusive shelf", r["Bookshelves"] == "read")
    check("exact score is preserved in Private Notes",
          "WA 9.42/10" in r["Private Notes"], r["Private Notes"])
    check("private note names the rank", "rank #3" in r["Private Notes"])
    check("review is NEVER written (it is public on Goodreads)", r["My Review"] == "")

    u = by_title["Unscored Book"]
    check("unscored read book has a BLANK rating, not a 1", u["My Rating"] == "",
          u["My Rating"])
    check("unscored read book has no private note", u["Private Notes"] == "")
    check("unscored read book keeps its date", u["Date Read"] == "2024/01/01")

    p = by_title["Piranesi"]
    check("to-read row is on the to-read shelf", p["Exclusive Shelf"] == "to-read")
    # THE one that matters most: a prediction must never reach a rating field.
    check("to-read row has NO rating even though a score was passed",
          p["My Rating"] == "", p["My Rating"])
    check("to-read row has NO private note (an 8.1 beside a blank star misleads)",
          p["Private Notes"] == "", p["Private Notes"])
    check("to-read row has no read date", p["Date Read"] == "")

    check("a titleless row is dropped",
          len(se.build_rows(read=[{"title": "  ", "score": 9}])) == 0)

    # The nonfiction track ranks by Total Average, and the note must say so —
    # a bare number in a foreign app a year later is not self-explanatory.
    nf = se.build_rows(read=[{"title": "SPQR", "author": "Mary Beard",
                              "score": 7.5, "rank": 2}],
                       score_label="Total Average")[0]
    check("score_label names the scale in the note",
          "Total Average 7.50/10" in nf["Private Notes"], nf["Private Notes"])


def test_dedupe_and_summary():
    print("\ndedupe + summarise — nothing dropped silently")
    dup = [{"title": "Piranesi", "author": "Susanna Clarke", "score": 8.0}]
    rows = se.build_rows(read=dup, to_read=dup)
    kept, dropped = se.dedupe(rows)
    check("a title in both shelves collapses to one row", len(kept) == 1, str(len(kept)))
    check("the RATED copy is the survivor", kept[0]["Exclusive Shelf"] == "read",
          kept[0]["Exclusive Shelf"])
    check("the drop is counted", dropped == 1, str(dropped))
    check("matching is case-insensitive on title+author",
          se.dedupe(se.build_rows(read=[
              {"title": "Piranesi", "author": "Susanna Clarke", "score": 8.0},
              {"title": "PIRANESI", "author": "susanna clarke", "score": 8.0}]))[1] == 1)

    s = se.summarise(se.dedupe(se.build_rows(read=READ, to_read=TBR))[0],
                     dropped_duplicate=2, skipped_in_progress=1)
    check("summary counts the read shelf", s["read"] == 2, str(s))
    check("summary counts the to-read shelf", s["to_read"] == 1, str(s))
    check("summary surfaces unrated read books", s["unrated_read"] == 1, str(s))
    check("summary carries the caller's drop counts",
          (s["dropped_duplicate"], s["skipped_in_progress"]) == (2, 1), str(s))
    check("summary total matches the rows", s["total"] == 3, str(s))


# ──────────────────────── the file itself + round trip ──────────────────────
def test_csv():
    print("\nto_csv — the header both importers were told to expect")
    text = se.to_csv(se.build_rows(read=READ, to_read=TBR))
    header = text.split("\r\n")[0]
    check("header is the Goodreads export header, verbatim and in order",
          header == ",".join(se.GOODREADS_COLUMNS), header)
    check("CRLF line endings (RFC 4180)", "\r\n" in text)
    check("no BOM", not text.startswith("﻿"))
    check("'Exclusive Shelf' is present — StoryGraph will not recognise the "
          "import without it", "Exclusive Shelf" in header)

    print("\nRound trip — build -> to_csv -> the real Goodreads import parser")
    rows, summary = gi.parse_goodreads_csv(text)
    check("every row survives the round trip", len(rows) == 3, str(summary))
    check("nothing was dropped on re-read",
          (summary["dropped_no_title"], summary["dropped_bad_shelf"],
           summary["dropped_dupe_in_csv"]) == (0, 0, 0), str(summary))
    check("shelves are read back correctly",
          summary["by_shelf"] == {"read": 2, "to-read": 1}, str(summary["by_shelf"]))

    back = {r["title"]: r for r in rows}
    cg = back["The Crippled God"]
    check("title/series/ordinal all survive",
          (cg["series"], cg["series_number"]) == ("Malazan: Book of the Fallen", 10),
          str((cg["series"], cg["series_number"])))
    check("author survives", cg["author"] == "Steven Erikson")
    check("star survives as 5", cg["goodreads_rating"] == 5, str(cg["goodreads_rating"]))
    check("year + month survive", (cg["year_read"], cg["read_month"]) == (2026, 5),
          str((cg["year_read"], cg["read_month"])))
    check("to-read book comes back unrated",
          back["Piranesi"]["goodreads_rating"] is None)
    check("unscored read book comes back unrated",
          back["Unscored Book"]["goodreads_rating"] is None)

    print("\nHostile text — quoting, injection, and unicode")
    nasty = [{"title": 'Comma, "Quote" and\nNewline',
              "author": "=cmd|'/c calc'!A1", "score": 6.0, "year_read": 2025,
              "read_month": 2},
             {"title": "Ödipus, Кафка 日本", "author": "Ünicode", "score": 8.0}]
    text2 = se.to_csv(se.build_rows(read=nasty))
    rows2, sum2 = gi.parse_goodreads_csv(text2)
    check("a title with comma/quote/newline round-trips intact",
          any(r["title"] == 'Comma, "Quote" and\nNewline' for r in rows2),
          str([r["title"] for r in rows2]))
    check("a formula-looking author is carried verbatim, not executed or mangled",
          any(r["author"] == "=cmd|'/c calc'!A1" for r in rows2))
    check("non-ASCII survives", any(r["title"] == "Ödipus, Кафка 日本" for r in rows2))
    check("row count intact under hostile text", sum2["kept"] == 2, str(sum2))


def main():
    print("=" * 70)
    print("shelf_export — Goodreads/StoryGraph library export")
    print("=" * 70)
    test_stars()
    test_join_series()
    test_dates()
    test_rows()
    test_dedupe_and_summary()
    test_csv()
    passed, total = sum(_results), len(_results)
    print("\n" + "=" * 70)
    print(f"{passed}/{total} checks passed")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
