#!/usr/bin/env python3
"""
sync_workbook.py — mirror the app's library INTO BookRankingsNew.xlsx (one way)
===============================================================================
The app grew out of `BookRankingsNew.xlsx`, but since the move to the database
the workbook has been frozen. This script catches it up so the spreadsheet keeps
working as a familiar *view* of the app's data.

DIRECTION IS ONE WAY: app -> workbook. Nothing here writes to any database; it
only SELECTs. The workbook is purely cosmetic — the app never reads it (the
engine's `source="db"` path and the walk-forward both ignore it), so a sync can
never change a prediction, a rank on the site, or anything stored.

WHAT IT WRITES (input cells only — every formula is left to Excel)
  * Recommendations   one row per app recommendation (read + unread): title,
                      genre, author, series, words, the 14 predicted components,
                      Done?, blurb, keywords. Existing rows keep their position;
                      new ones are appended; rows the app no longer has drop out.
                      A read book with no recommendation row gets one (metadata
                      only, blank predictions) so the lookups that feed
                      TotalRankings still find its genre/author/series/words.
  * TotalRankings     Book + the 14 rated components for every read fiction
                      book, ordered by the app's WA (Rank is =ROW()-1).
  * TBRFinished       the read titles (everything else there is XLOOKUPs).
  * Timeline          Book / Year / Month / MonthNum in reading order (read_seq).
                      The 2025/2026 BooksRead + TierList sheets derive from it.
  * SeriesRankings    series names + _SeriesID, ordered by the app's series
                      score (so the sheet's =ROW()-1 Rank matches the site).
  * <year>BooksRead   only where the book list is typed in by hand (2025): that
                      year's books by WA. 2026's list is a Timeline formula and
                      is left alone. Writing 2025's list also overwrites rows
                      75+, which held a copy-pasted 2026 formula that leaked
                      2026 books into the 2025 sheet once 2026 passed 73 reads.
  Formula columns are filled down by Excel when a sheet needs more rows.

  NOT synced: nonfiction (the sheet's Table6 is the retired 8-component
  layout), the ReadNext queue, DeltaTracker, GenreWeights / GCompWeights.

WHY EXCEL DOES THE WRITING
  The workbook carries 16 charts, data-validation dropdowns (x14 extensions),
  structured tables and ~60k formulas. openpyxl drops charts and those
  extensions on save, so this script never saves the file with openpyxl — it
  reads the current state with openpyxl (read-only, for the diff) and then
  drives Microsoft Excel over AppleScript to make the edits, recalculate, and
  save. Requires macOS + Microsoft Excel. The first run may ask you to allow
  your terminal to control Excel.

SAFETY
  * Dry run by default: prints what would change, touches nothing.
  * `--write` refuses if the workbook is already open in Excel (so unsaved
    edits of yours can't be overwritten), then copies the file to
    BookRankingsNew.xlsx.backup-<timestamp> (gitignored) before editing.

USAGE
    python3 scripts/sync_workbook.py              # dry run against the LIVE app
    python3 scripts/sync_workbook.py --write      # back up, then update the workbook
    python3 scripts/sync_workbook.py --local      # use local books.db instead of live

The live source reads DATABASE_URL from the environment or the project `.env`
(the same lookup as scripts/refresh_from_live.py); it is never printed.
"""
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

WORKBOOK = os.path.join(ROOT, "BookRankingsNew.xlsx")

# Sheet column -> app component, in the workbook's own layout (identical in
# Recommendations and TotalRankings; the gaps are category-average formulas).
COMPONENT_BLOCKS = [
    ("I", ["Plot", "Entertainment", "Action", "Ending"]),
    ("O", ["Depth", "Emotional Impact", "Motivations"]),
    ("T", ["Prose", "Narration"]),
    ("X", ["Insights", "Thought-Provokingness"]),
    ("AB", ["Depth2", "Integration", "Originality"]),
]
COMPONENTS = [c for _, comps in COMPONENT_BLOCKS for c in comps]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
NON_SERIES = {"", "standalone", "none", "n/a"}

# Hard row capacities, set by the workbook's own fixed-size formula ranges
# (e.g. COUNTIF(TotalRankings!$C$2:$C$401, ...)). Past these the sheet's
# formulas would silently ignore rows, so the sync refuses instead.
TOTALRANKINGS_LAST_ROW = 401
TIMELINE_LAST_ROW = 300


# ---------------------------------------------------------------------------
# Small pure helpers (unit-tested in test_sync_workbook.py)
# ---------------------------------------------------------------------------
def col_to_num(col):
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def num_to_col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"',
                         "”": '"'})


def norm_title(t):
    """Match key for titles: whitespace-, case- and curly-quote-insensitive
    (the library and the recommendations disagree on "Death’s End")."""
    return " ".join(str(t or "").translate(_QUOTES).split()).casefold()


def score_cell(v):
    """App component -> sheet cell. Stored 0 / NULL / NaN means "not scored"
    and is written blank, which the sheet's IF($X2="",...) guards expect."""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if f != f or f == 0:          # NaN or the 0 = "not scored" sentinel
        return ""
    return round(f, 4)


def series_cell(s):
    s = (s or "").strip().strip("'\"")
    return "Standalone" if s.lower() in NON_SERIES else s


def as_literal(v):
    """Python value -> AppleScript literal. "" clears a cell."""
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return '""'
        return repr(int(v)) if v.is_integer() else repr(v)
    s = str(v).replace("\r", " ").replace("\n", " ")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def grid_literal(rows):
    return "{" + ", ".join(
        "{" + ", ".join(as_literal(v) for v in row) + "}" for row in rows) + "}"


def contiguous_groups(cols):
    """[1,2,3,7,8,12] -> [(1,3),(7,8),(12,12)]"""
    out = []
    for c in sorted(cols):
        if out and c == out[-1][1] + 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return [tuple(g) for g in out]


# ---------------------------------------------------------------------------
# App data (SELECT only)
# ---------------------------------------------------------------------------
def load_app_data(source, user_id=None):
    if source == "live":
        from refresh_from_live import load_database_url
        os.environ["DB_BACKEND"] = "postgres"
        os.environ["DATABASE_URL"] = load_database_url()
        os.environ.setdefault("DB_POOL", "0")
    import db_backend
    import db_loader
    import user_weights
    import views

    uid = user_id or db_backend.DEFAULT_USER_ID
    # Same frame the site's engine builds (per-user weight overrides included),
    # so the WA used for ordering is the WA the app shows.
    books, _, _ = db_loader.load_from_db(
        user_id=uid, weight_overrides=user_weights.load_overrides(uid))

    con = db_backend.connect(readonly=True)
    try:
        order = {norm_title(t): (seq, month) for t, seq, month in con.execute(
            "SELECT title, read_seq, read_month FROM books WHERE user_id=?",
            (uid,)).fetchall()}
        comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
        rec_rows = con.execute(
            "SELECT title, genre, author, series, words, done, blurb, keywords, "
            f"{comp_cols} FROM recommendations WHERE user_id=? ORDER BY id",
            (uid,)).fetchall()
        meta_rows = con.execute(
            "SELECT series, complete FROM series_meta WHERE user_id=?",
            (uid,)).fetchall()
    finally:
        con.close()

    recs = []
    for r in rec_rows:
        rec = {"title": str(r[0]).strip(), "genre": r[1], "author": r[2],
               "series": r[3], "words": r[4], "done": bool(r[5]),
               "blurb": r[6] or "", "keywords": r[7] or ""}
        rec.update(dict(zip(COMPONENTS, r[8:])))
        recs.append(rec)

    series_meta = {s: {"complete": bool(c)} for s, c in meta_rows}
    sa = views.series_aggregate(books, series_meta=series_meta)
    series_order = [] if sa.empty else [str(s) for s in sa["Series"]]
    return books, order, recs, series_order


# ---------------------------------------------------------------------------
# Target state for each sheet
# ---------------------------------------------------------------------------
def build_plan(books, order, recs, series_order, current_rec_titles):
    """Return {sheet: {"rows": [...], ...}} — the input values to write.
    `current_rec_titles` is the Recommendations sheet's present title order,
    used only to keep surviving rows where they are."""
    read = []
    for _, b in books.iterrows():
        read.append({
            "title": str(b["Book"]).strip(), "genre": b["Genre"],
            "author": b["Author"], "series": b["Series"],
            "words": b["Words"], "year": b["Year"], "wa": float(b["WA"]),
            **{c: b.get(c) for c in COMPONENTS},
        })
    read_by_key = {norm_title(b["title"]): b for b in read}

    # --- Recommendations: app rows, keyed by title, sheet order preserved ---
    by_key, dupes = {}, []
    for r in recs:
        k = norm_title(r["title"])
        if k in by_key:
            dupes.append(r["title"])
            continue
        by_key[k] = r
    for k, b in read_by_key.items():          # read books lacking a rec row
        if k not in by_key:
            by_key[k] = {"title": b["title"], "genre": b["genre"],
                         "author": b["author"], "series": b["series"],
                         "words": b["words"], "done": True, "blurb": "",
                         "keywords": "", **{c: None for c in COMPONENTS}}
    ordered, seen = [], set()
    for t in current_rec_titles:
        k = norm_title(t)
        if k in by_key and k not in seen:
            ordered.append(k)
            seen.add(k)
    ordered += [k for k in by_key if k not in seen]

    rec_rows = []
    for k in ordered:
        r = dict(by_key[k])
        b = read_by_key.get(k)
        if b:   # a read book's metadata comes from the library, and it's done
            r.update(title=b["title"], genre=b["genre"], author=b["author"],
                     series=b["series"], words=b["words"], done=True)
        rec_rows.append(r)

    # --- TotalRankings: read books by app WA, best first ---
    ranked = sorted(read, key=lambda b: (-b["wa"], b["title"]))

    # --- Timeline: reading order, oldest first (the sheet reverses it) ---
    def seq_key(b):
        seq = order.get(norm_title(b["title"]), (None, None))[0]
        return (seq is None, seq or 0, b["title"])
    timeline = []
    for b in sorted(read, key=seq_key):
        month = order.get(norm_title(b["title"]), (None, None))[1]
        month = int(month) if month else None
        timeline.append({"title": b["title"], "year": b["year"],
                         "month": MONTHS[month - 1] if month else "",
                         "monthnum": month or ""})

    return {"recs": rec_rows, "ranked": ranked, "timeline": timeline,
            "series": series_order, "dupes": dupes}


# ---------------------------------------------------------------------------
# Current workbook state (openpyxl, READ ONLY — never saved)
# ---------------------------------------------------------------------------
def read_workbook_state(path):
    from openpyxl import load_workbook
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(path, read_only=True)

    def column(ws, col, first, last):
        c = col_to_num(col)
        return [row[0] for row in ws.iter_rows(min_row=first, max_row=last,
                                               min_col=c, max_col=c,
                                               values_only=True)]

    def is_formula(v):
        return (isinstance(v, str) and v.startswith("=")) or (
            v is not None and type(v).__name__ == "ArrayFormula")

    def template(ws, row, first_col, last_col):
        """(formula column numbers in `row`)"""
        vals = next(ws.iter_rows(min_row=row, max_row=row, min_col=first_col,
                                 max_col=last_col, values_only=True))
        return [first_col + i for i, v in enumerate(vals) if is_formula(v)]

    def last_formula_row(ws, col, first, cap):
        vals = column(ws, col, first, cap)
        rows = [first + i for i, v in enumerate(vals) if is_formula(v)]
        return max(rows) if rows else first - 1

    st = {}
    ws = wb["Recommendations"]
    titles = column(ws, "D", 2, 2000)
    st["rec_titles"] = [t for t in titles if t not in (None, "")]
    st["rec_used"] = 1 + max((i + 1 for i, t in enumerate(titles)
                              if t not in (None, "")), default=0)
    st["rec_formula_last"] = last_formula_row(ws, "C", 2, 2000)
    st["rec_formula_cols"] = template(ws, 2, 1, col_to_num("CO"))
    st["rec_done"] = {norm_title(t): d for t, d in zip(
        titles, column(ws, "AJ", 2, 2000)) if t}

    ws = wb["TotalRankings"]
    tr_titles = column(ws, "D", 2, TOTALRANKINGS_LAST_ROW)
    st["tr_titles"] = [t for t in tr_titles if t not in (None, "")]
    tr_scores = {}
    for start, comps in COMPONENT_BLOCKS:
        c0 = col_to_num(start)
        for i, row in enumerate(ws.iter_rows(
                min_row=2, max_row=TOTALRANKINGS_LAST_ROW, min_col=c0,
                max_col=c0 + len(comps) - 1, values_only=True)):
            t = tr_titles[i]
            if t:
                tr_scores.setdefault(norm_title(t), {}).update(
                    {c: score_cell(v) for c, v in zip(comps, row)})
    st["tr_scores"] = tr_scores

    ws = wb["TBRFinished"]
    st["tbr_used"] = 1 + sum(1 for t in column(ws, "D", 2, 1000)
                             if t not in (None, ""))
    st["tbr_formula_last"] = last_formula_row(ws, "E", 2, 1000)
    st["tbr_formula_cols"] = template(ws, 2, 1, col_to_num("AJ"))

    ws = wb["Timeline"]
    st["tl_used"] = 5 + sum(1 for t in column(ws, "T", 6, TIMELINE_LAST_ROW)
                            if t not in (None, ""))
    st["tl_formula_last"] = last_formula_row(ws, "X", 6, TIMELINE_LAST_ROW)

    ws = wb["SeriesRankings"]
    names = column(ws, "E", 2, 500)
    st["series"] = [s for s in names if s not in (None, "")]
    st["series_used"] = 1 + max((i + 1 for i, s in enumerate(names)
                                 if s not in (None, "")), default=0)
    st["series_formula_last"] = last_formula_row(ws, "B", 2, 500)
    st["series_formula_cols"] = [c for c in template(ws, 2, 1, col_to_num("AI"))
                                 if c != col_to_num("AG")]

    # Year sheets whose book list (column D) is typed in, not a formula.
    st["year_sheets"] = {}
    for ws in wb.worksheets:
        title = ws.title
        if not (title.endswith("BooksRead") and title[:4].isdigit()):
            continue
        d = column(ws, "D", 2, 1000)
        if not d or d[0] in (None, "") or is_formula(d[0]):
            continue
        rows = len(d)                     # read-only sheets end at max_row
        st["year_sheets"][title] = {"year": int(title[:4]), "rows": rows,
                                    "titles": [t for t in d if isinstance(t, str)
                                               and not t.startswith("=")]}
    wb.close()
    return st


# ---------------------------------------------------------------------------
# Dry-run report
# ---------------------------------------------------------------------------
def report(plan, st):
    old_tr = {norm_title(t) for t in st["tr_titles"]}
    new_read = [b["title"] for b in plan["ranked"]
                if norm_title(b["title"]) not in old_tr]
    gone_read = sorted(set(st["tr_titles"]) - {b["title"] for b in plan["ranked"]},
                       key=str)
    rescored = []
    for b in plan["ranked"]:
        old = st["tr_scores"].get(norm_title(b["title"]))
        if old and any(old.get(c) != score_cell(b.get(c)) for c in COMPONENTS):
            rescored.append(b["title"])
    old_recs = {norm_title(t) for t in st["rec_titles"]}
    new_recs = [r["title"] for r in plan["recs"]
                if norm_title(r["title"]) not in old_recs]
    gone_recs = len(old_recs - {norm_title(r["title"]) for r in plan["recs"]})
    newly_done = [r["title"] for r in plan["recs"] if r["done"] and
                  st["rec_done"].get(norm_title(r["title"])) is False]
    new_series = [s for s in plan["series"] if s not in set(st["series"])]
    gone_series = [s for s in st["series"] if s not in set(plan["series"])]

    def show(label, items, limit=12):
        print(f"  {label}: {len(items)}")
        for t in items[:limit]:
            print(f"      - {t}")
        if len(items) > limit:
            print(f"      … and {len(items) - limit} more")

    print(f"Read books        app {len(plan['ranked'])}  ·  workbook "
          f"{len(st['tr_titles'])}")
    show("new read books to add", new_read)
    show("ratings that differ", rescored)
    show("in workbook but not in app (will drop)", gone_read)
    print(f"Recommendations   app {len(plan['recs'])}  ·  workbook "
          f"{len(st['rec_titles'])}")
    show("new recommendations", new_recs, limit=8)
    show("marked read since the workbook", newly_done, limit=8)
    print(f"  dropping (no longer in app): {gone_recs}")
    print(f"Series            app {len(plan['series'])}  ·  workbook "
          f"{len(st['series'])}")
    show("new series", new_series)
    show("not a series in the app (will drop)", gone_series)
    for sheet, info in st["year_sheets"].items():
        want = [b["title"] for b in plan["ranked"] if b["year"] == info["year"]]
        print(f"{sheet:<17} app {len(want)}  ·  workbook {len(info['titles'])}"
              + ("" if want == info["titles"] else "  (will rewrite)"))
    years = sorted({b["year"] for b in plan["ranked"] if b["year"]})
    missing = [y for y in years if y not in (2025, 2026)]
    if missing:
        print(f"  note: read years {missing} have no year sheet in the workbook "
              "(they still appear in TotalRankings and Timeline)")
    if plan["dupes"]:
        print(f"  note: duplicate recommendation titles kept once: "
              f"{plan['dupes']}")


# ---------------------------------------------------------------------------
# Excel automation
# ---------------------------------------------------------------------------
CHUNK = 150   # rows per AppleScript `set value` (keeps each script small)


def run_applescript(src, timeout=600):
    p = subprocess.run(["osascript", "-"], input=src, text=True,
                       capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"Excel automation failed: {p.stderr.strip()}")
    return p.stdout.strip()


class Excel:
    def __init__(self, name):
        self.name = name
        self.ops = []

    def ws(self, sheet):
        return f'worksheet "{sheet}" of workbook "{self.name}"'

    def write(self, sheet, start_col, first_row, rows, width):
        """Write `rows` (each `width` long) starting at start_col/first_row."""
        c0 = col_to_num(start_col)
        end_col = num_to_col(c0 + width - 1)
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i:i + CHUNK]
            r0 = first_row + i
            rng = f"{start_col}{r0}:{end_col}{r0 + len(chunk) - 1}"
            self.ops.append(f'set value of range "{rng}" of {self.ws(sheet)} '
                            f"to {grid_literal(chunk)}")

    def fill_down(self, sheet, cols, template_row, last_row):
        """Copy the formulas in `template_row` down to `last_row`."""
        if last_row <= template_row:
            return
        for a, b in contiguous_groups(cols):
            rng = f"{num_to_col(a)}{template_row}:{num_to_col(b)}{last_row}"
            self.ops.append(f'fill down range "{rng}" of {self.ws(sheet)}')

    def script(self):
        body = "\n    ".join(self.ops)
        return f'''tell application "Microsoft Excel"
  set oldCalc to calculation
  set calculation to calculation manual
  try
    {body}
  on error errMsg number errNum
    set calculation to oldCalc
    error errMsg number errNum
  end try
  set calculation to oldCalc
  calculate
end tell'''


def pad(rows, n, width):
    return rows + [[""] * width for _ in range(max(0, n - len(rows)))]


def apply(plan, st, path):
    name = os.path.basename(path)
    open_names = run_applescript(
        'tell application "Microsoft Excel" to get name of every workbook')
    if name in [s.strip() for s in open_names.split(",")]:
        raise SystemExit(f"{name} is open in Excel — save and close it, then "
                         "re-run (so unsaved edits can't be overwritten).")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.backup-{stamp}"
    shutil.copy2(path, backup)
    print(f"Backup: {os.path.basename(backup)}")

    xl = Excel(name)

    # Recommendations ------------------------------------------------------
    recs = plan["recs"]
    n = len(recs)
    last = 1 + n
    xl.fill_down("Recommendations", st["rec_formula_cols"],
                 st["rec_formula_last"], last)
    span = max(n, st["rec_used"] - 1)
    xl.write("Recommendations", "D", 2, pad([[
        r["title"], r["genre"] or "", r["author"] or "",
        series_cell(r["series"]), r["words"] or "",
        *[score_cell(r.get(c)) for c in COMPONENT_BLOCKS[0][1]]]
        for r in recs], span, 9), 9)
    for start, comps in COMPONENT_BLOCKS[1:]:
        xl.write("Recommendations", start, 2, pad(
            [[score_cell(r.get(c)) for c in comps] for r in recs],
            span, len(comps)), len(comps))
    xl.write("Recommendations", "AJ", 2, pad(
        [[bool(r["done"]), r["blurb"], r["keywords"]] for r in recs],
        span, 3), 3)

    # TotalRankings ----------------------------------------------------------
    ranked = plan["ranked"]
    cap = TOTALRANKINGS_LAST_ROW - 1
    if len(ranked) > cap:
        raise SystemExit(f"{len(ranked)} read books exceeds TotalRankings' "
                         f"{cap}-row capacity — the sheet's formulas stop at "
                         f"row {TOTALRANKINGS_LAST_ROW}.")
    xl.write("TotalRankings", "D", 2, pad([[b["title"]] for b in ranked],
                                          cap, 1), 1)
    for start, comps in COMPONENT_BLOCKS:
        xl.write("TotalRankings", start, 2, pad(
            [[score_cell(b.get(c)) for c in comps] for b in ranked],
            cap, len(comps)), len(comps))

    # TBRFinished ----------------------------------------------------------------
    xl.fill_down("TBRFinished", st["tbr_formula_cols"], st["tbr_formula_last"],
                 1 + len(ranked))
    xl.write("TBRFinished", "D", 2, pad(
        [[t["title"]] for t in plan["timeline"]],
        max(len(ranked), st["tbr_used"] - 1), 1), 1)

    # Timeline -------------------------------------------------------------------
    tl = plan["timeline"]
    if 5 + len(tl) > TIMELINE_LAST_ROW:
        raise SystemExit(f"{len(tl)} books exceeds the Timeline's capacity.")
    xl.fill_down("Timeline", [col_to_num("X")], st["tl_formula_last"], 5 + len(tl))
    xl.write("Timeline", "T", 6, pad([
        [t["title"], t["year"] or "", t["month"], t["monthnum"]] for t in tl],
        max(len(tl), st["tl_used"] - 5), 4), 4)

    # SeriesRankings -------------------------------------------------------------
    ser = plan["series"]
    xl.fill_down("SeriesRankings", st["series_formula_cols"],
                 st["series_formula_last"], 1 + len(ser))
    span = max(len(ser), st["series_used"] - 1)
    xl.write("SeriesRankings", "E", 2, pad([[s] for s in ser], span, 1), 1)
    xl.write("SeriesRankings", "AG", 2, pad(
        [[i] for i in range(1, len(ser) + 1)], span, 1), 1)

    # Hand-typed year sheets (e.g. 2025BooksRead): that year's books by WA ---
    for sheet, info in st["year_sheets"].items():
        yr = [[b["title"]] for b in ranked if b["year"] == info["year"]]
        if len(yr) > info["rows"]:
            raise SystemExit(f"{sheet}: {len(yr)} books exceeds its "
                             f"{info['rows']} rows.")
        xl.write(sheet, "D", 2, pad(yr, info["rows"], 1), 1)

    posix = path.replace('"', '\\"')
    run_applescript(f'''tell application "Microsoft Excel"
  open (POSIX file "{posix}")
end tell''', timeout=300)
    try:
        run_applescript(xl.script(), timeout=1800)
        run_applescript(f'''tell application "Microsoft Excel"
  save workbook "{name}"
end tell''', timeout=600)
    finally:
        run_applescript(f'''tell application "Microsoft Excel"
  if (name of every workbook) contains "{name}" then close workbook "{name}" saving no
end tell''', timeout=300)
    print(f"Updated {name} ({len(xl.ops)} Excel operations).")


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--write", action="store_true",
                    help="back up the workbook, then update it via Excel")
    ap.add_argument("--local", action="store_true",
                    help="read local books.db instead of the live app")
    ap.add_argument("--user", default=None, help="tenant id (default: owner)")
    ap.add_argument("--workbook", default=WORKBOOK)
    args = ap.parse_args(argv)

    source = "local" if args.local else "live"
    print(f"Source: {'local books.db' if args.local else 'live app (read-only)'}")
    books, order, recs, series_order = load_app_data(source, args.user)
    st = read_workbook_state(args.workbook)
    plan = build_plan(books, order, recs, series_order, st["rec_titles"])
    report(plan, st)
    if not args.write:
        print("\nDry run — nothing changed. Re-run with --write to update the "
              "workbook.")
        return 0
    if sys.platform != "darwin":
        raise SystemExit("--write drives Microsoft Excel and needs macOS.")
    print()
    apply(plan, st, os.path.abspath(args.workbook))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
