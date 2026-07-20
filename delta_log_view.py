"""
delta_log_view.py
=================
Read-only presentation logic for the Delta Log page (`GET /api/delta-log`).

The Delta Log is a HISTORICAL accuracy record: "how did the prediction compare
to the rating, for books I had forecast before reading them." Rows accumulate in
the `delta_log` table from three write paths, and only one of them is a genuine
predicted-vs-actual pair:

  1. live `_maybe_log_delta` (on add/finish a book) — act_* is the REAL rating.   ✓
  2. `repredict_on_add.py`  — logs old→new prediction MOVES for still-unread
     same-author/genre peers, tagged ``baseline_repredict:<trigger>``. Here act_*
     is a NEW PREDICTION, not a rating, and the book has not been read.           ✗
  3. one-time backfills — ``retro_sweep_v1_shrunk`` (a leave-one-out
     reconstruction) and the workbook backfill (``logged_at`` == the backfill
     marker). Both are genuine predicted-vs-actual for read books, but the pred
     was reconstructed in bulk, not captured on the read date.                    ✓(reconstructed)

The raw table therefore surfaces unread books (path 2) and lists most read books
twice (a path-1/3 row plus a path-3 row). This module enforces what the page is
supposed to show:

  REQUIREMENT 1 — only genuinely-read books.
    A row is kept only when its title matches a book the tenant has actually
    FINISHED (``books.status = 'finished'``) AND the row is not a
    ``baseline_repredict:*`` audit row. Both conditions are required:
      • read-state alone would keep a stale ``baseline_repredict`` row once its
        book is later read (its act_* is still a re-prediction, not a rating);
      • the tag filter alone would miss untagged non-read rows.
    The predicate keys off the explicit finished state — never off "an act_*
    value exists," because the repredict/backfill rows have those too.

  REQUIREMENT 2 — one authoritative row per book.
    When a finished book has several genuine rows, keep exactly one, preferring
    a live-logged row over the workbook backfill over the retro_sweep
    reconstruction (a live row captures the true read-time forecast; the workbook
    pred is the historically-recorded forecast; retro_sweep is a later
    reconstruction). Ties break to the most recent row.

Pure and side-effect-free: it takes already-fetched rows plus the finished-title
set and returns the rows to show. No database, no engine, no prediction math — so
it is trivially unit-testable and identical across the SQLite and Postgres
backends. The stored ``pred_*`` values are passed through UNCHANGED: the Delta
Log's prediction is frozen at log time and never recomputed here.
"""

# Priority for dedup — LOWER is more authoritative (kept over higher).
_PRIORITY_LIVE = 0        # live-logged at read time — the true read-time forecast
_PRIORITY_BACKFILL = 1    # workbook backfill — historically-recorded forecast
_PRIORITY_RETRO = 2       # retro_sweep leave-one-out reconstruction

_REPREDICT_PREFIX = "baseline_repredict:"
_RETRO_PREFIX = "retro_sweep"


def _norm(title):
    """Case/whitespace-insensitive title key — mirrors the match used by
    `_maybe_log_delta` and the dequeue logic in backend/main.py."""
    return (title or "").strip().lower()


def _is_repredict(entry):
    """True for a `repredict_on_add` audit row, whose act_* is a re-prediction
    (not a rating) and whose book is, at log time, still unread."""
    return (entry.get("tag") or "").startswith(_REPREDICT_PREFIX)


def _priority(entry, backfill_marker):
    """Dedup rank for a genuine row (lower = kept)."""
    tag = entry.get("tag") or ""
    if tag.startswith(_RETRO_PREFIX):
        return _PRIORITY_RETRO
    if backfill_marker is not None and entry.get("logged_at") == backfill_marker:
        return _PRIORITY_BACKFILL
    return _PRIORITY_LIVE


def visible_rows(entries, finished_titles, backfill_marker, read_order=None):
    """Filter + dedup delta_log rows for the Delta Log page.

    entries: list of row dicts, each with at least ``id``, ``title``,
             ``logged_at`` and ``tag`` (plus the pred_/act_/d_ columns, passed
             through untouched).
    finished_titles: set of normalized (``strip().lower()``) titles the tenant
             has finished (``books.status = 'finished'``).
    backfill_marker: the ``logged_at`` sentinel stamped on workbook-backfill rows
             (``db_write.DELTA_BACKFILL_MARKER``); may be None.
    read_order: optional ``{normalized-title: (year, month)}`` map of each book's
             read date. When given, the returned rows are ordered by reading
             chronology — LEAST-recently-read first, most-recently-read last —
             so the page reads oldest→newest. A book with an unknown month sorts
             after the dated books of its year; a book with no read date at all
             sorts last. When ``read_order`` is None, rows fall back to
             newest-logged-first (by ``id`` descending). The dedup below is
             independent of this ordering.

    Returns the rows to display, at most one per book, in the order above.
    """
    finished = set(finished_titles or ())
    best = {}
    for e in entries:
        if _is_repredict(e):
            continue                       # Req 1: never a re-prediction audit row
        key = _norm(e.get("title"))
        if key not in finished:
            continue                       # Req 1: only genuinely-finished books
        cur = best.get(key)
        # Req 2: prefer the more authoritative row, then the most recent.
        if cur is None or (
            (_priority(e, backfill_marker), -(e.get("id") or 0))
            < (_priority(cur, backfill_marker), -(cur.get("id") or 0))
        ):
            best[key] = e
    rows = list(best.values())
    if read_order is None:
        # Legacy default: newest-logged first.
        return sorted(rows, key=lambda e: (e.get("id") or 0), reverse=True)
    # Chronological: least-recently-read → most-recently-read. Missing month sorts
    # after its year's dated books (13); missing date sorts last (9999). id is a
    # stable within-month tiebreak (ascending ≈ insertion order).
    def _chrono_key(e):
        ym = read_order.get(_norm(e.get("title")))
        if not ym:
            return (9999, 13, e.get("id") or 0)
        year, month = ym
        return (year if year is not None else 9999,
                month if month is not None else 13,
                e.get("id") or 0)
    return sorted(rows, key=_chrono_key)
