"""
backend/main.py — FastAPI wrapper around the existing Python engine.
Run from the project root: uvicorn backend.main:app --reload --port 8000
The engine modules (db_loader, db_write, predict_engine) must be importable,
which they are when you run from the BookRankingAPP directory.

─────────────────────────────────────────────────────────────────────────────
SECURITY POSTURE — localhost single-user only
─────────────────────────────────────────────────────────────────────────────
This server is designed to run on 127.0.0.1 (localhost) for one user. It has
NO authentication and NO authorisation. Every write/delete endpoint (POST
/api/books, DELETE /api/books/{title}, POST /api/queue, etc.) is intentionally
open — that is safe on loopback but catastrophically unsafe on a network.

DO NOT:
  • bind uvicorn to 0.0.0.0 or any non-loopback address
  • put this behind a reverse proxy that exposes it publicly
  • deploy to a remote server

...without first adding authentication and tightening CORS to an explicit
allowlist. The CORS origin and bind host are read from environment variables
(ALLOWED_ORIGIN, BIND_HOST) so a deliberate change is visible and auditable;
the defaults are the safe localhost values and must not be altered here.
─────────────────────────────────────────────────────────────────────────────
"""

import sys
import os
import math
import io
import contextlib
import json
import re
import sqlite3
from contextlib import asynccontextmanager

# Make the project root importable regardless of where uvicorn is launched from
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)  # books.db is resolved relative to cwd

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

import db_loader
import db_write
import predict_engine as pe
import views as views_mod
import validate_engine as ve
import nonfiction_engine as nfe

# research_predict is optional: it requires apikey.txt and heavy LLM deps.
# Imported at module level so the import cost is paid once, not per request.
# Handlers that need it check `_rp` is not None before using.
try:
    import research_predict as _rp
    import research_layer as _rl
    import nonfiction_research as _nr
except ImportError:
    _rp = None  # server starts fine; LLM endpoints return 503
    _rl = None
    _nr = None

# Hybrid per-component sourcing (data-driven policy). Separately guarded so a
# failure here never disables the core research path; predict falls back to
# pure-memory scores if it is unavailable or disabled.
try:
    import hybrid_researcher as _hybrid
except Exception:
    _hybrid = None


# ─────────────────────────────────────────────────────────────────────────────
# ENGINE CACHE
# ─────────────────────────────────────────────────────────────────────────────
# The engine tuple (books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream) is
# expensive to produce: it reads the DB, fits a regression, and computes genre
# bias. We build it once at startup and serve all endpoints from the cache.
# Write endpoints call _invalidate_engine() after a successful db_write so the
# next read reflects the change.

_engine_cache: Optional[tuple] = None


def _get_engine() -> tuple:
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = pe.build(source="db")
    return _engine_cache


def _invalidate_engine() -> None:
    global _engine_cache
    _engine_cache = pe.build(source="db")


# Nonfiction engine cache — the (books, gw, gcw) tuple from the SEPARATE
# nonfiction engine. Built lazily; rebuilt after any nonfiction write.
_nf_engine_cache: Optional[tuple] = None


def _get_nf_engine() -> tuple:
    global _nf_engine_cache
    if _nf_engine_cache is None:
        _nf_engine_cache = nfe.load_nonfiction_from_db()
    return _nf_engine_cache


def _invalidate_nf_engine() -> None:
    global _nf_engine_cache
    _nf_engine_cache = nfe.load_nonfiction_from_db()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    _invalidate_engine()  # warm cache at startup
    yield


app = FastAPI(title="Reading Ledger API", version="1.0", lifespan=lifespan)

# Safe defaults: localhost only. Override via env vars only for deliberate,
# network-aware deployments that have also added authentication.
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:3000")
_BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")  # informational; enforced by uvicorn CLI

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _clean(val):
    """Convert NaN/inf to None so JSON serialization doesn't fail."""
    if val is None:
        return None
    try:
        if math.isnan(val) or math.isinf(val):
            return None
    except TypeError:
        pass
    return val


def _norm_snum(num):
    """Normalize a stored series_number: None stays None, whole values become
    int (so JSON shows 6 not 6.0), fractional values (0.5, 3.5) stay float."""
    if num is None:
        return None
    return int(num) if float(num) == int(num) else float(num)


def _series_number_map(table: str) -> dict:
    """Return {lowercased-title: series_number} for a table. Used to attach
    ordinals to engine-backed responses (db_loader is read-only and doesn't
    carry series_number). series_number may be int or float (0.5 prequels)."""
    con = sqlite3.connect(db_write.DB)
    try:
        rows = con.execute(
            f"SELECT title, series_number FROM {table} "
            f"WHERE series_number IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    out = {}
    for title, num in rows:
        if title is None or num is None:
            continue
        out[title.strip().lower()] = _norm_snum(num)
    return out


def _lookup_series_meta(client, title: str, author_hint: str = "unknown") -> dict:
    """Ask the LLM for a book's author, series name, and ordinal — the single
    meta-prompt path shared by /api/lookup and /api/predict/research. Returns
    {"author": str, "series": str, "series_number": int|None}. series_number is
    None when standalone/unknown. Never raises — on failure returns blanks."""
    meta_prompt = (
        f'Return ONLY a JSON object with these keys:\n'
        f'  "author": the correct full author name for "{title}"\n'
        f'  "series": the series name if the book belongs to one (empty string if standalone)\n'
        f'  "series_number": the number within the series as an integer (0 if standalone or unknown)\n'
        f'Respond with raw JSON only, no markdown.'
    )
    try:
        meta_msg = client.messages.create(
            model=_rp.rm.MODEL, max_tokens=200,
            messages=[{"role": "user", "content": meta_prompt}],
        )
        meta = _rl._extract_json(meta_msg.content[0].text.strip())
    except Exception:
        return {"author": author_hint, "series": "", "series_number": None}
    author = (meta.get("author") or author_hint).strip() or author_hint
    s_name = (meta.get("series") or "").strip()
    s_num = int(meta.get("series_number", 0) or 0)
    return {
        "author": author,
        "series": s_name,
        "series_number": s_num if (s_name and s_num > 0) else None,
    }


@app.get("/api/books")
def get_books():
    """Return all rated books with their WA, metadata, and component scores."""
    books, gw, gcw = _get_engine()[:3]
    category_components = books.attrs["category_components"]
    snum_map = _series_number_map("books")

    # Convert to a list of dicts that JSON can handle cleanly
    result = []
    for _, row in books.iterrows():
        book = {
            "title": row["Book"],
            "author": row["Author"],
            "genre": row["Genre"],
            "series": row.get("Series") or "",
            "series_number": snum_map.get((row["Book"] or "").strip().lower()),
            "words": _clean(row.get("Words")),
            "year": _clean(row.get("Year")),
            "year_read": _clean(row.get("Year")),
            "wa": round(float(row["WA"]), 4),
            "components": {},
            "category_avgs": {
                cat: round(float(row.get("W" + cat, 0) or 0), 4)
                for cat in db_loader.CATEGORY_OF_INTEREST
            },
        }
        for cat, comps in category_components.items():
            book["components"][cat] = {}
            for comp in comps:
                v = row.get(comp)
                book["components"][cat][comp] = _clean(
                    round(float(v), 2) if v is not None else None
                )
        result.append(book)

    # Sort by WA descending — client can re-sort, but default is the ranking
    result.sort(key=lambda b: b["wa"], reverse=True)
    for i, b in enumerate(result):
        b["rank"] = i + 1

    return {
        "books": result,
        "genres": sorted(set(b["genre"] for b in result)),
        "category_order": list(category_components.keys()),
    }


@app.get("/api/genres")
def get_genres():
    """Distinct genres in the rated library."""
    books = _get_engine()[0]
    return sorted(books["Genre"].dropna().unique().tolist())


@app.get("/api/valid-genres")
def get_valid_genres():
    """All genres defined in genre_weights (valid for adding new books)."""
    con = sqlite3.connect(db_write.DB)
    genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()
    return genres


@app.get("/api/books/{title}/scores")
def get_book_scores(title: str):
    """Return component scores for a single rated book (for Edit Ratings)."""
    books = _get_engine()[0]
    row = books[books["Book"] == title]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"Book '{title}' not found")
    row = row.iloc[0]
    cat_comps = books.attrs["category_components"]
    components: dict = {}
    for cat, comps in cat_comps.items():
        components[cat] = {}
        for comp in comps:
            v = row.get(comp)
            components[cat][comp] = _clean(round(float(v), 2) if v is not None else None)
    return {
        "title": row["Book"],
        "author": row["Author"],
        "genre": row["Genre"],
        "wa": round(float(row["WA"]), 4),
        "components": components,
    }


class AddBookRequest(BaseModel):
    title: str
    genre: str
    author: str
    scores: dict[str, float]
    series: Optional[str] = None
    series_number: Optional[int] = None
    words: Optional[int] = None
    year_read: Optional[int] = None


@app.post("/api/books")
def add_book(req: AddBookRequest):
    """Add a newly-rated book via db_write.add_book, then dequeue it."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.add_book(
                req.title, req.genre, req.author, req.scores,
                series=req.series or None,
                series_number=req.series_number or None,
                words=req.words or None,
                year_read=req.year_read,
            )
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not add book.")

    # Remove the finished book from the queue so slots advance automatically.
    try:
        con = sqlite3.connect(db_write.DB)
        current_queue = [t for (t,) in con.execute(
            "SELECT title FROM read_queue ORDER BY position")]
        con.close()
        title_lower = req.title.strip().lower()
        new_queue = [t for t in current_queue
                     if t.strip().lower() != title_lower]
        if len(new_queue) < len(current_queue):
            db_write.update_queue(new_queue)
    except Exception:
        pass  # dequeue failure is non-fatal; book was still added

    _invalidate_engine()

    # If this title had a stored prediction, record the delta automatically.
    # Non-fatal: a failure here never rolls back the successful add_book.
    try:
        _maybe_log_delta(req.title, req.scores)
    except Exception:
        pass

    return {"ok": True, "message": out.replace("✓", "").strip()}


def _maybe_log_delta(title: str, act_scores: dict) -> None:
    """Check recommendations for a stored prediction and log delta if found."""
    con = sqlite3.connect(db_write.DB)
    row = con.execute(
        "SELECT genre, " + ", ".join(f'"{c}"' for c in db_write.FICTION_COMPONENTS)
        + ' FROM recommendations WHERE LOWER(title)=LOWER(?) ORDER BY id DESC LIMIT 1',
        (title,)
    ).fetchone()
    con.close()
    if row is None:
        return  # no prediction on record

    genre = row[0]
    pred_scores = dict(zip(db_write.FICTION_COMPONENTS, row[1:]))
    if not any(v is not None for v in pred_scores.values()):
        return  # recommendation exists but has no component scores

    # Compute pred_wa by running the same WA formula as db_loader
    books, gw, gcw = _get_engine()[:3]
    wcats = {
        cat: db_loader._weighted_cat_avg(pred_scores, genre, cat, gcw)
        for cat in db_loader.CATEGORY_OF_INTEREST
    }
    pred_wa = sum(wcats[cat] * (gw.get(genre, {}).get(cat) or 0)
                  for cat in db_loader.CATEGORY_OF_INTEREST)

    # act_wa: pull the just-inserted book from the freshly-rebuilt engine
    match = books[books["Book"].str.lower() == title.lower()]
    if match.empty:
        return
    act_wa = float(match.iloc[0]["WA"])

    db_write.log_delta(title, pred_scores, pred_wa, act_scores, act_wa)


class EditRatingRequest(BaseModel):
    scores: dict[str, float]


@app.post("/api/books/{title}/scores")
def edit_rating(title: str, req: EditRatingRequest):
    """Update component scores for an existing book via db_write.change_rating."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.change_rating(title, req.scores)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not update rating.")
    _invalidate_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


class LookupRequest(BaseModel):
    title: str
    author_hint: Optional[str] = None


@app.post("/api/lookup")
def lookup_book(req: LookupRequest):
    """
    Title-only metadata lookup: calls the LLM to find author, genre, estimated
    word count, series, and a blurb. Genre is constrained to the genre_weights
    list. Returns the raw lookup result for the user to confirm before filling.
    """
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")

    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not initialise LLM client: {e}")

    con = sqlite3.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()

    hint_author = req.author_hint.strip() if req.author_hint else "unknown"
    title = req.title.strip()

    try:
        _scores_raw, _conf, blurb, _keywords, det_genre, words_raw = \
            _rp.research_rich_plus(
                client, title, hint_author, None,
                allowed_genres=allowed_genres,
            )

        meta = _lookup_series_meta(client, title, hint_author)

        return {
            "title": title,
            "author": meta["author"],
            "genre": det_genre,
            "words": words_raw,
            "series": meta["series"],
            "series_number": meta["series_number"],
            "blurb": blurb or "",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Look-up failed: {e}")


@app.get("/api/tiers")
def get_tiers(year: Optional[int] = None):
    """Return books with tier assignments (S+/S/A/B/C/D/F), optionally filtered by year_read."""
    books = _get_engine()[0]
    category_components = books.attrs["category_components"]
    snum_map = _series_number_map("books")

    if year is not None:
        books = books[books["Year"] == year]

    books = books.sort_values("WA", ascending=False).reset_index(drop=True)

    SPLUS_THRESHOLD = 9.5
    BAND_FRACTIONS = [("S", 0.09), ("A", 0.15), ("B", 0.25), ("C", 0.25), ("D", 0.15), ("F", 0.10)]
    TIER_ORDER = ["S+", "S", "A", "B", "C", "D", "F"]

    n = len(books)
    n_splus = int((books["WA"] >= SPLUS_THRESHOLD).sum())
    remaining = n - n_splus

    bounds, acc = [], 0.0
    for name, frac in BAND_FRACTIONS:
        acc += frac
        bounds.append((name, int(round(acc * remaining))))

    tiers = []
    for i in range(n):
        if i < n_splus:
            tiers.append("S+")
            continue
        j = i - n_splus
        placed = "F"
        for name, b in bounds:
            if j < b:
                placed = name
                break
        tiers.append(placed)

    result = []
    for i, ((_, row), tier) in enumerate(zip(books.iterrows(), tiers)):
        book = {
            "title": row["Book"],
            "author": row["Author"],
            "genre": row["Genre"],
            "series": row.get("Series") or "",
            "series_number": snum_map.get((row["Book"] or "").strip().lower()),
            "words": _clean(row.get("Words")),
            "year_read": _clean(row.get("Year")),
            "wa": round(float(row["WA"]), 4),
            "rank": i + 1,
            "tier": tier,
            "components": {},
        }
        for cat, comps in category_components.items():
            book["components"][cat] = {}
            for comp in comps:
                v = row.get(comp)
                book["components"][cat][comp] = _clean(
                    round(float(v), 2) if v is not None else None
                )
        result.append(book)

    counts = {t: sum(1 for b in result if b["tier"] == t) for t in TIER_ORDER}

    return {
        "books": result,
        "tier_counts": counts,
        "tier_order": TIER_ORDER,
        "category_order": list(category_components.keys()),
    }


@app.delete("/api/books/{title}")
def delete_book(title: str):
    """Permanently delete a rated book via db_write.delete_book (backup-protected)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.delete_book(title)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not delete book.")
    _invalidate_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.delete("/api/recommendations/{title}")
def delete_recommendation(title: str):
    """Permanently delete a TBR recommendation via db_write.delete_recommendation."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.delete_recommendation(title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not delete recommendation.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.get("/health")
def health():
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# READ QUEUE — mood-filtered recommendations
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/queue")
def get_queue():
    """Return the ordered read-queue titles."""
    con = sqlite3.connect(db_write.DB)
    titles = [r[0] for r in con.execute(
        "SELECT title FROM read_queue ORDER BY position")]
    con.close()
    return {"titles": titles}


class UpdateQueueRequest(BaseModel):
    titles: list[str]


@app.post("/api/queue")
def update_queue(req: UpdateQueueRequest):
    """Replace the read queue with the given ordered list of titles."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.update_queue(req.titles)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "message": buf.getvalue().strip().replace("✓", "").strip()}


class AddSeriesRequest(BaseModel):
    series_name: str


@app.post("/api/queue/add-series")
def add_series_to_queue(req: AddSeriesRequest):
    """
    Resolve a series name via LLM, then append the unread books (in reading
    order) to the end of the current queue. Books not already in the TBR or
    read tables are added to recommendations (no scores). Already-read books
    are skipped. Returns a summary of what happened.
    """
    series_name = req.series_name.strip()
    if not series_name:
        raise HTTPException(status_code=422, detail="Series name is required.")

    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")

    con = sqlite3.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))

    # Fetch existing data for de-dupe checks
    read_titles = {t.strip().lower() for (t,) in con.execute("SELECT title FROM books")}
    tbr_titles = {t.strip().lower() for (t,) in con.execute("SELECT title FROM recommendations WHERE done=0")}
    current_queue = [t for (t,) in con.execute("SELECT title FROM read_queue ORDER BY position")]
    queue_set = {t.strip().lower() for t in current_queue}
    con.close()

    # ── LLM: resolve series → ordered book list ───────────────────────────
    genres_str = ", ".join(allowed_genres)
    prompt = f"""You are a book-data assistant. Return ONLY a JSON object — no prose, no markdown.

Series name: "{series_name}"

If the series name is ambiguous or does not match a known book series, return:
{{"ambiguous": true, "reason": "brief explanation"}}

Otherwise return:
{{
  "ambiguous": false,
  "series_canonical": "canonical series name",
  "books": [
    {{"title": "...", "author": "...", "genre": "...", "words": 123456, "order": 1}},
    ...
  ]
}}

Rules:
- Use the standard reading order (publication order, or chronological if that is the convention for this series).
- "genre" must be one of these exact values: {genres_str}
- "words" is an integer word count estimate (null if unknown).
- "order" is 1-indexed reading position.
- Include every main-series entry. Omit novellas and short stories unless they are essential to the main plot.
- Do not include any text outside the JSON object."""

    try:
        msg = client.messages.create(
            model=_rp.DISCOVER_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        data = _rl._extract_json(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")

    if data.get("ambiguous"):
        return {
            "ok": False,
            "ambiguous": True,
            "message": data.get("reason", "Series name is ambiguous — please be more specific."),
        }

    books = data.get("books", [])
    if not books:
        return {
            "ok": False,
            "ambiguous": True,
            "message": "No books found for that series — please check the name and try again.",
        }

    series_canonical = data.get("series_canonical", series_name)
    books.sort(key=lambda b: b.get("order", 999))

    already_read = []
    already_tbr = []
    newly_added = []
    skipped_errors = []
    to_append = []  # titles in order to append to queue

    for book in books:
        title = (book.get("title") or "").strip()
        author = (book.get("author") or "").strip()
        genre = (book.get("genre") or "").strip()
        words = book.get("words")
        if not title or not author:
            continue

        title_lower = title.lower()

        # Skip already-read books
        if title_lower in read_titles:
            already_read.append(title)
            continue

        # Already in TBR
        if title_lower in tbr_titles:
            already_tbr.append(title)
            # Still append to queue if not already there
            if title_lower not in queue_set:
                to_append.append(title)
            continue

        # Add to TBR (no scores — series bulk-add)
        if genre not in allowed_genres:
            genre = allowed_genres[0] if allowed_genres else "Fantasy"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ok = db_write.add_recommendation(
                    title, genre, author, scores={},
                    series=series_canonical,
                    words=int(words) if words else None,
                    done=0,
                    require_scores=False,
                )
        except Exception as e:
            skipped_errors.append(f"{title}: {e}")
            continue
        if ok:
            newly_added.append(title)
            tbr_titles.add(title_lower)
            if title_lower not in queue_set:
                to_append.append(title)
        else:
            skipped_errors.append(f"{title}: {buf.getvalue().strip()}")

    # Append to queue
    if to_append:
        new_queue = current_queue + to_append
        buf2 = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf2):
                db_write.update_queue(new_queue)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Queue update failed: {e}")

    summary_parts = []
    total = len(already_read) + len(already_tbr) + len(newly_added)
    if already_read:
        summary_parts.append(f"{len(already_read)} already read and skipped")
    if newly_added:
        summary_parts.append(f"{len(newly_added)} newly added to your TBR")
    if already_tbr:
        summary_parts.append(f"{len(already_tbr)} already in your TBR")
    appended_count = len(to_append)

    if appended_count == 0 and not already_read:
        message = f"All books from {series_canonical} are already in your queue."
    else:
        detail = " · ".join(summary_parts) if summary_parts else ""
        message = f"Added {appended_count} book{'s' if appended_count != 1 else ''} from {series_canonical} to the queue"
        if detail:
            message += f" — {detail}"
        message += "."

    return {
        "ok": True,
        "ambiguous": False,
        "series_canonical": series_canonical,
        "total_books": total,
        "already_read": len(already_read),
        "already_tbr": len(already_tbr),
        "newly_added": len(newly_added),
        "appended_to_queue": appended_count,
        "appended_titles": to_append,
        "message": message,
        "errors": skipped_errors,
    }


@app.get("/api/read-queue")
def get_read_queue():
    """Return all not-done recommendations with flat component scores and predicted rank."""
    books, gw, gcw = _get_engine()[:3]
    rated_wa = books["WA"].values

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = sqlite3.connect(db_write.DB)
    rows = con.execute(
        f'SELECT title, author, genre, series, series_number, words, blurb, keywords, {comp_cols} '
        f'FROM recommendations WHERE done=0'
    ).fetchall()
    con.close()

    result = []
    for r in rows:
        title, author, genre, series, series_number, words, blurb, keywords = r[:8]
        comp_vals = dict(zip(COMPONENTS, r[8:]))

        components = {
            c: _clean(float(v)) if v is not None else None
            for c, v in comp_vals.items()
        }

        genre_str = (genre or "Unknown").strip()
        wa = 0.0
        category_avgs = {}
        for cat in db_loader.CATEGORY_OF_INTEREST:
            wcat = db_loader._weighted_cat_avg(comp_vals, genre_str, cat, gcw)
            category_avgs[cat] = round(wcat, 4)
            wa += wcat * ((gw.get(genre_str, {}) or {}).get(cat, 0) or 0)

        predicted_rank = int((rated_wa > wa).sum() + 1)

        result.append({
            "title": (title or "").strip(),
            "author": (author or "").strip(),
            "genre": genre_str,
            "series": (series or "").strip().strip("'\""),
            "series_number": _norm_snum(series_number),
            "words": words,
            "blurb": blurb or "",
            "keywords": keywords or "",
            "components": components,
            "wa": round(wa, 4),
            "predicted_rank": predicted_rank,
            "category_avgs": category_avgs,
        })

    genres = sorted(set(r["genre"] for r in result if r["genre"]))
    return {"recommendations": result, "genres": genres}


# ─────────────────────────────────────────────────────────────────────────────
# PREDICT — instant analog estimate (free) and grounded research (LLM)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/predict/instant")
def predict_instant(title: str, author: str, genre: str):
    """Free instant analog prediction — no API call, uses rated-book analogs."""
    try:
        data = _get_engine()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine build failed: {e}")
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data
    g_info = ginfo.get(genre, {})
    if genre not in {row for row in books_e["Genre"].unique()}:
        raise HTTPException(status_code=422, detail=f"Genre '{genre}' not recognised.")
    try:
        p = pe.predict(title, author, genre, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")
    return {
        "title": title, "author": author, "genre": genre,
        "wa_final": round(p["wa_final"], 4),
        "ci": [round(p["ci"][0], 4), round(p["ci"][1], 4)],
        "rank": p["rank"], "rank_range": list(p["rank_range"]),
        "total": p["total"],
        "src": p["src"], "n_src": p["n_src"],
        "n_genre": g_info.get("n", 0),
        "wcats": {k: round(float(v), 4) for k, v in p["wcats"].items()},
        "wa_model": round(p["wa_model"], 4),
        "bias": round(p["bias"], 4),
        "trust": round(p["trust"], 4),
        "analog_mean": round(p["analog_mean"], 4),
        "r2": round(p["r2"], 4),
        "resid_sd": round(p["resid_sd"], 4),
        "est": {k: round(float(v), 4) for k, v in p["est"].items()},
    }


class ResearchRequest(BaseModel):
    title: str
    author: str
    genre: Optional[str] = None   # None → auto-detect from the LLM
    grounded: bool = False        # False → fast memory scores; True → hybrid
                                  # (web-grounded) upgrade. Default is fast so the
                                  # candidate list scores instantly; the client
                                  # re-requests grounded=True to refine per book.


@app.post("/api/predict/research")
def predict_research(req: ResearchRequest):
    """
    Grounded research prediction: research_rich_plus → correlation-smooth →
    author+genre correct → WA roll-up. One LLM API call (or cache hit).
    Returns corrected components, WA, CI, rank, grounding signals.
    """
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")

    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Client error: {e}")

    try:
        data = _get_engine()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine build failed: {e}")
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data

    con = sqlite3.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()

    cache = _rp.load_cache()
    try:
        scores, conf, blurb, keywords, det_genre, words, from_cache = _rp.research_book(
            req.title, req.author, req.genre, client, cache,
            allowed_genres=allowed_genres,
        )
        _rp.save_cache(cache)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Research failed: {e}")

    eff_genre = req.genre or det_genre
    if eff_genre is None:
        raise HTTPException(status_code=422,
                            detail="Could not auto-detect a genre — pick one manually.")

    # HYBRID SOURCING (progressive): only when the caller asks for the grounded
    # upgrade (req.grounded). The default fast path returns memory scores so the
    # candidate list scores instantly; the client then re-requests grounded=True
    # per book to refine it in the background (~110s web call, cached). Sourcing
    # only — the same `scores` dict flows through correct_and_predict unchanged;
    # falls back to memory on any web failure.
    grounding_on = _hybrid is not None and _hybrid.HYBRID_SOURCING_DEFAULT
    applied_grounded = False
    if grounding_on and req.grounded:
        try:
            scores = _hybrid.apply_grounded_overrides(
                req.title, req.author, eff_genre, scores)
            applied_grounded = True
        except Exception:
            applied_grounded = False  # keep pure-memory scores if web fails

    try:
        corr_models = _rp.build_corr_models(books_e, cache)
        res = _rp.correct_and_predict(
            req.title, req.author, eff_genre, scores, conf, resid_sd,
            books_e, gw_e, gcw_e, cache, blurb=blurb, keywords=keywords,
            corr_models=corr_models,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Correction failed: {e}")

    # Category averages from corrected components (for display)
    cat_comps = books_e.attrs["category_components"]
    components_by_cat: dict = {}
    for cat, comps in cat_comps.items():
        components_by_cat[cat] = {c: _clean(round(res["scores"].get(c, 0), 2)) for c in comps}

    # NOTE: the rich house-style blurb and the series/ordinal lookup are NOT done
    # here — they each cost an extra LLM call, and scoring many discover candidates
    # would multiply that. Both are deferred to /api/recommendations (save time),
    # so they're only paid for books the reader actually keeps. The plain research
    # blurb below is what's shown while browsing; save upgrades it.
    return {
        "title": res["title"], "author": res["author"], "genre": res["genre"],
        "wa": round(res["wa"], 4),
        "ci": [round(res["ci"][0], 4), round(res["ci"][1], 4)],
        "rank": res["rank"], "total": res["total"],
        "n_genre": res["n_genre"], "n_author": res["n_author"],
        "conf": res["conf"],
        "from_cache": from_cache,
        "words": words,
        "series": "",
        "series_number": None,
        "blurb": res.get("blurb", ""),
        "keywords": res.get("keywords", ""),
        "components": components_by_cat,
        "category_order": list(cat_comps.keys()),
        "genre_auto_detected": req.genre is None,
        "sourcing": "hybrid" if applied_grounded else "memory",
        "hybrid_available": bool(grounding_on and not applied_grounded),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER — generate candidates then score them individually
# ─────────────────────────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    request: str
    # Optional upper bound. When omitted, the LLM infers the count from the
    # request wording (e.g. "the 5 main books of X" → 5).
    max_candidates: Optional[int] = None


@app.post("/api/discover/candidates")
def discover_candidates(req: DiscoverRequest):
    """Generate candidate book titles for a free-text request (1 API call)."""
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")

    books = _get_engine()[0]
    cache = _rp.load_cache()

    con = sqlite3.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    tbr_books = [(t or "", a or "") for t, a in con.execute(
        "SELECT title, author FROM recommendations")]
    con.close()

    read_books = list(zip(books["Book"].tolist(), books["Author"].tolist()))

    try:
        result = _rp.generate_candidates(
            req.request.strip(), allowed_genres, read_books,
            tbr_books=tbr_books, n=req.max_candidates, client=client,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Candidate generation failed: {e}")

    candidates = result["candidates"]
    # Flag which are already in cache (free to score)
    for c in candidates:
        c["cached"] = c.get("title", "") in cache

    return {"candidates": candidates, "request": req.request.strip(),
            "note": result.get("note", ""), "sources": result.get("sources", [])}


class SaveRecommendationRequest(BaseModel):
    title: str
    genre: str
    author: str
    scores: dict[str, float]
    words: Optional[int] = None
    blurb: Optional[str] = None
    keywords: Optional[str] = None
    series: Optional[str] = None
    series_number: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE BLURB & KEYWORDS
# ─────────────────────────────────────────────────────────────────────────────

class GenerateMetaRequest(BaseModel):
    title: str
    author: str
    genre: str


@app.post("/api/recommendations/generate-meta")
def generate_recommendation_meta(req: GenerateMetaRequest):
    """Generate blurb + keywords for a recommendation that was added without research."""
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Client error: {e}")
    try:
        blurb, keywords = _rp.generate_blurb_keywords(req.title, req.author, req.genre, client)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")
    if not blurb and not keywords:
        raise HTTPException(status_code=422,
                            detail="Model returned nothing usable for this book — try again.")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.set_recommendation_meta(req.title, blurb or None, keywords or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "blurb": blurb or "", "keywords": keywords or ""}


# ─────────────────────────────────────────────────────────────────────────────
# READING STATS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/reading/stats")
def get_reading_stats():
    """Reading stats: totals, per-year, by-genre, by-author breakdowns."""
    books = _get_engine()[0]
    rs = views_mod.reading_stats(books)
    s = rs["summary"]

    per_year = []
    for _, row in rs["per_year"].iterrows():
        per_year.append({
            "year": int(row["Year"]),
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "avg_total_average": _clean(round(float(row["Avg Total Average"]), 2)),
            "avg_words": _clean(round(float(row["Avg Words"]), 0)) if row["Avg Words"] == row["Avg Words"] else None,
        })

    by_genre = []
    for _, row in rs["by_genre"].iterrows():
        by_genre.append({
            "genre": row["Genre"],
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "avg_total_average": _clean(round(float(row["Avg Total Average"]), 2)),
            "avg_words": _clean(round(float(row["Avg Words"]), 0)) if row["Avg Words"] == row["Avg Words"] else None,
        })

    by_author = []
    for _, row in rs["by_author"].iterrows():
        by_author.append({
            "author": row["Author"],
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
        })

    return {
        "summary": {
            "total_books": s["total_books"],
            "avg_wa": _clean(round(s["avg_wa"], 2)),
            "avg_total_average": _clean(round(s["avg_total_average"], 2)),
            "avg_words": _clean(round(s["avg_words"], 0)) if s["avg_words"] == s["avg_words"] else None,
        },
        "per_year": per_year,
        "by_genre": by_genre,
        "by_author": by_author,
    }


# ─────────────────────────────────────────────────────────────────────────────
# READING STATUS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/reading/status")
def get_reading_status():
    """Queue-derived reading status: last read, currently reading, reading next."""
    books, gw, gcw = _get_engine()[:3]
    rated_wa = books["WA"].values
    total_rated = len(books)

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)

    con = sqlite3.connect(db_write.DB)

    # Queue positions 1 and 2
    queue_titles = [r[0].strip() for r in con.execute(
        "SELECT title FROM read_queue ORDER BY position LIMIT 2").fetchall()]

    def _slot_from_rec(title: str):
        """Build a status slot from the recommendations table."""
        row = con.execute(
            f'SELECT author, genre, series, series_number, words, {comp_cols} '
            f'FROM recommendations WHERE LOWER(TRIM(title))=LOWER(TRIM(?))',
            (title,)
        ).fetchone()
        if row is None:
            # In queue but not in recommendations — show name only, no scores
            return {
                "title": title, "author": "", "genre": "", "series": "",
                "series_number": None,
                "has_prediction": False,
                "wa": None, "rank": None, "total": total_rated,
                "category_avgs": {},
            }
        author, genre, series, series_number, words = row[:5]
        comp_vals = dict(zip(COMPONENTS, row[5:]))
        has_scores = any(v is not None for v in comp_vals.values())
        if not has_scores:
            return {
                "title": title,
                "author": (author or "").strip(),
                "genre": (genre or "").strip(),
                "series": (series or "").strip().strip("'\""),
                "series_number": _norm_snum(series_number),
                "has_prediction": False,
                "wa": None, "rank": None, "total": total_rated,
                "category_avgs": {},
            }
        genre_str = (genre or "Unknown").strip()
        wa = 0.0
        category_avgs = {}
        for cat in db_loader.CATEGORY_OF_INTEREST:
            wcat = db_loader._weighted_cat_avg(comp_vals, genre_str, cat, gcw)
            category_avgs[cat] = round(wcat, 2)
            wa += wcat * ((gw.get(genre_str, {}) or {}).get(cat, 0) or 0)
        predicted_rank = int((rated_wa > wa).sum() + 1)
        return {
            "title": title,
            "author": (author or "").strip(),
            "genre": genre_str,
            "series": (series or "").strip().strip("'\""),
            "series_number": _norm_snum(series_number),
            "has_prediction": True,
            "wa": round(wa, 2),
            "rank": predicted_rank,
            "total": total_rated,
            "category_avgs": category_avgs,
        }

    currently_reading = _slot_from_rec(queue_titles[0]) if len(queue_titles) >= 1 else None
    reading_next = _slot_from_rec(queue_titles[1]) if len(queue_titles) >= 2 else None

    # Last read: most recently inserted row in books (by rowid)
    last_row = con.execute(
        "SELECT title FROM books ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    con.close()

    last_read = None
    if last_row:
        lr_title = (last_row[0] or "").strip()
        match = books[books["Book"].str.strip().str.lower() == lr_title.lower()]
        if not match.empty:
            brow = match.iloc[0]
            wa_val = float(brow["WA"])
            rank = int((rated_wa > wa_val).sum() + 1)
            category_avgs = {
                cat: round(float(brow["W" + cat]), 2)
                for cat in db_loader.CATEGORY_OF_INTEREST
            }
            last_read = {
                "title": lr_title,
                "author": str(brow["Author"]),
                "genre": str(brow["Genre"]),
                "series": str(brow["Series"]),
                "series_number": _series_number_map("books").get(lr_title.lower()),
                "has_prediction": False,
                "wa": round(wa_val, 2),
                "rank": rank,
                "total": total_rated,
                "category_avgs": category_avgs,
            }

    return {
        "last_read": last_read,
        "currently_reading": currently_reading,
        "reading_next": reading_next,
    }


class SetYearRequest(BaseModel):
    title: str
    year: int


@app.post("/api/reading/set-year")
def set_year_read(req: SetYearRequest):
    """Set year_read on a rated book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_year_read(req.title, req.year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not set year.")
    _invalidate_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


# ─────────────────────────────────────────────────────────────────────────────
# SERIES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/series")
def get_series():
    """Series rankings: per-series aggregates sorted by Adjusted WA."""
    books = _get_engine()[0]
    sa = views_mod.series_aggregate(books)
    if sa.empty:
        return {"series": []}
    result = []
    for _, row in sa.iterrows():
        result.append({
            "rank": int(row["Rank"]),
            "series": row["Series"],
            "author": row["Author"],
            "genre": row["Genre"],
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "adjusted_wa": _clean(round(float(row["Adjusted WA"]), 3)),
            "avg_total_average": _clean(round(float(row["Avg Total Average"]), 2)),
        })
    return {"series": result}


@app.get("/api/series/tiers")
def get_series_tiers():
    """Series tier list: same bands as book tier list but by Adjusted WA (S+ >= 9.0)."""
    books = _get_engine()[0]
    sa = views_mod.series_aggregate(books)
    if sa.empty:
        return {"series": [], "tier_order": views_mod.TIER_ORDER, "tier_counts": {}}
    sa_renamed = sa.rename(columns={"Adjusted WA": "Total Average"})
    tiered = views_mod.tier_bands(sa_renamed, "Total Average", 9.0)
    result = []
    for _, row in tiered.iterrows():
        result.append({
            "series": row["Series"],
            "author": row["Author"],
            "genre": row["Genre"],
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "adjusted_wa": _clean(round(float(row["Total Average"]), 3)),
            "avg_total_average": _clean(round(float(row["Avg Total Average"]), 2)),
            "tier": row["Tier"],
        })
    counts = views_mod.tier_counts(tiered)
    return {"series": result, "tier_order": views_mod.TIER_ORDER, "tier_counts": counts}


# ─────────────────────────────────────────────────────────────────────────────
# TIMELINE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/timeline")
def get_timeline():
    """Per-year reading timeline: book count, avg WA, five category averages."""
    books = _get_engine()[0]
    tl = views_mod.timeline(books)
    if tl.empty:
        return {"rows": [], "categories": views_mod.CATEGORY_ORDER}
    rows = []
    for _, row in tl.iterrows():
        rec = {
            "year": int(row["Year"]),
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "avg_words": _clean(round(float(row["Avg Words"]), 0)) if row["Avg Words"] == row["Avg Words"] else None,
        }
        for cat in views_mod.CATEGORY_ORDER:
            rec[cat.lower()] = _clean(round(float(row[cat]), 2)) if row[cat] == row[cat] else None
        rows.append(rec)
    return {"rows": rows, "categories": views_mod.CATEGORY_ORDER}


# ═════════════════════════════════════════════════════════════════════════════
# NONFICTION — parallel endpoints over the SEPARATE nonfiction engine/table.
# Same JSON shapes as the fiction endpoints (so the frontend reuses the same
# components by `kind`), with two differences the frontend keys off: nonfiction
# carries `total_average` and is ranked/tiered by it (not WA), and the category
# set is Quality / Aesthetics / Theme. Never touches the fiction engine.
# ═════════════════════════════════════════════════════════════════════════════

NF_CAT_ORDER = nfe.NONFICTION_CATEGORY_ORDER  # ["Quality", "Aesthetics", "Theme"]


def _nf_book_dict(row, cat_components, snum_map):
    """Shape one nonfiction row like the fiction book dict, plus total_average."""
    wa = row.get("WA")
    book = {
        "title": row["Book"],
        "author": row["Author"],
        "genre": row["Genre"],
        "series": row.get("Series") or "",
        "series_number": snum_map.get((row["Book"] or "").strip().lower()),
        "words": _clean(row.get("Words")),
        "year": _clean(row.get("Year")),
        "year_read": _clean(row.get("Year")),
        "wa": _clean(round(float(wa), 4)) if wa is not None and wa == wa else None,
        "total_average": _clean(round(float(row["Total Average"]), 4))
        if row["Total Average"] == row["Total Average"] else None,
        "components": {},
        "category_avgs": {
            cat: _clean(round(float(row.get("W" + cat, 0) or 0), 4))
            for cat in NF_CAT_ORDER
        },
    }
    for cat in NF_CAT_ORDER:
        book["components"][cat] = {}
        for comp in cat_components.get(cat, []):
            v = row.get(comp)
            book["components"][cat][comp] = _clean(
                round(float(v), 2) if v is not None and v == v else None
            )
    return book


@app.get("/api/nonfiction/books")
def get_nf_books():
    """All nonfiction books, ranked by Total Average (the workbook's nonfiction
    ranking). Carries both `total_average` and the Quality-lean `wa`."""
    books, gw, gcw = _get_nf_engine()
    bt = nfe.add_total_average(books)
    cat_components = books.attrs["category_components"]
    snum_map = _series_number_map("nonfiction_books")
    result = [_nf_book_dict(row, cat_components, snum_map) for _, row in bt.iterrows()]
    result.sort(key=lambda b: (b["total_average"] is not None,
                               b["total_average"] or 0.0), reverse=True)
    for i, b in enumerate(result):
        b["rank"] = i + 1
    return {
        "books": result,
        "genres": sorted({b["genre"] for b in result if b["genre"]}),
        "category_order": list(NF_CAT_ORDER),
    }


@app.get("/api/nonfiction/tiers")
def get_nf_tiers():
    """Nonfiction tier list, banded by Total Average (reuses the fiction
    thresholds: S+ >= 9.5, then 9/15/25/25/15/10% percentiles)."""
    books, gw, gcw = _get_nf_engine()
    bt = nfe.add_total_average(books)
    cat_components = books.attrs["category_components"]
    snum_map = _series_number_map("nonfiction_books")
    if bt.empty:
        return {"books": [], "tier_counts": {}, "tier_order": views_mod.TIER_ORDER,
                "category_order": list(NF_CAT_ORDER)}
    tiered = nfe.tier_bands(bt, "Total Average", 9.5)
    result = []
    for i, (_, row) in enumerate(tiered.iterrows()):
        b = _nf_book_dict(row, cat_components, snum_map)
        b["rank"] = i + 1
        b["tier"] = row["Tier"]
        result.append(b)
    counts = {t: sum(1 for b in result if b["tier"] == t) for t in views_mod.TIER_ORDER}
    return {
        "books": result,
        "tier_counts": counts,
        "tier_order": views_mod.TIER_ORDER,
        "category_order": list(NF_CAT_ORDER),
    }


@app.get("/api/nonfiction/series")
def get_nf_series():
    """Nonfiction series rollup (ranked by Avg Total Average). Normally empty —
    nonfiction has no series yet."""
    books = _get_nf_engine()[0]
    sa = nfe.series_aggregate(books)
    if sa.empty:
        return {"series": []}
    result = []
    for _, row in sa.iterrows():
        result.append({
            "rank": int(row["Rank"]),
            "series": row["Series"],
            "author": row["Author"],
            "genre": "Nonfiction",
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "adjusted_wa": _clean(round(float(row["Avg Total Average"]), 3)),
            "avg_total_average": _clean(round(float(row["Avg Total Average"]), 2)),
        })
    return {"series": result}


@app.get("/api/nonfiction/series/tiers")
def get_nf_series_tiers():
    """Nonfiction series tier list. Normally empty (no nonfiction series yet)."""
    books = _get_nf_engine()[0]
    sa = nfe.series_aggregate(books)
    if sa.empty:
        return {"series": [], "tier_order": views_mod.TIER_ORDER, "tier_counts": {}}
    tiered = nfe.tier_bands(sa.rename(columns={"Avg Total Average": "Total Average"}),
                            "Total Average", 9.0)
    result = []
    for _, row in tiered.iterrows():
        result.append({
            "series": row["Series"], "author": row["Author"], "genre": "Nonfiction",
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "adjusted_wa": _clean(round(float(row["Total Average"]), 3)),
            "avg_total_average": _clean(round(float(row["Total Average"]), 2)),
            "tier": row["Tier"],
        })
    return {"series": result, "tier_order": views_mod.TIER_ORDER,
            "tier_counts": nfe.tier_counts(tiered)}


@app.get("/api/nonfiction/timeline")
def get_nf_timeline():
    """Per-year nonfiction timeline (Quality/Aesthetics/Theme). Normally empty —
    the migrated nonfiction books have no year_read."""
    books = _get_nf_engine()[0]
    tl = nfe.timeline(books)
    cats = list(NF_CAT_ORDER)
    if tl.empty:
        return {"rows": [], "categories": cats}
    rows = []
    for _, row in tl.iterrows():
        rec = {
            "year": int(row["Year"]),
            "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "avg_words": None,
        }
        for cat in cats:
            rec[cat.lower()] = _clean(round(float(row[cat]), 2)) if row[cat] == row[cat] else None
        rows.append(rec)
    return {"rows": rows, "categories": cats}


@app.get("/api/nonfiction/reading/stats")
def get_nf_reading_stats():
    """Nonfiction reading stats. by_genre is omitted (no nonfiction genre
    taxonomy yet); by_author carries the breakdown."""
    books = _get_nf_engine()[0]
    rs = nfe.reading_stats(books)
    s = rs["summary"]
    per_year = []
    for _, row in rs["per_year"].iterrows():
        per_year.append({
            "year": int(row["Year"]), "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
            "avg_total_average": _clean(round(float(row["Avg Total Average"]), 2)),
            "avg_words": None,
        })
    by_author = []
    for _, row in rs["by_author"].iterrows():
        by_author.append({
            "author": row["Author"], "books": int(row["Books"]),
            "avg_wa": _clean(round(float(row["Avg WA"]), 2)),
        })
    return {
        "summary": {
            "total_books": s["total_books"],
            "avg_wa": _clean(round(s["avg_wa"], 2)) if s["avg_wa"] == s["avg_wa"] else None,
            "avg_total_average": _clean(round(s["avg_total_average"], 2))
            if s["avg_total_average"] == s["avg_total_average"] else None,
            "avg_words": _clean(round(s["avg_words"], 0)) if s["avg_words"] == s["avg_words"] else None,
        },
        "per_year": per_year,
        "by_genre": [],
        "by_author": by_author,
    }


@app.get("/api/nonfiction/reading/status")
def get_nf_reading_status():
    """Nonfiction reading status. currently-reading / reading-next come from the
    nonfiction_books.status column (there is no nonfiction queue); last_read is
    the most recently added nonfiction book."""
    books = _get_nf_engine()[0]
    bt = nfe.add_total_average(books)
    total = int(len(bt))
    ta_vals = bt["Total Average"].values
    snum = _series_number_map("nonfiction_books")

    def slot_for(title):
        if not title:
            return None
        m = bt[bt["Book"].str.strip().str.lower() == title.strip().lower()]
        if m.empty:
            return None
        r = m.iloc[0]
        tav = float(r["Total Average"])
        return {
            "title": r["Book"], "author": str(r["Author"]), "genre": str(r["Genre"]),
            "series": str(r.get("Series") or ""),
            "series_number": snum.get((r["Book"] or "").strip().lower()),
            "has_prediction": False,
            "wa": _clean(round(float(r["WA"]), 2)) if r["WA"] == r["WA"] else None,
            "total_average": _clean(round(tav, 2)),
            "rank": int((ta_vals > tav).sum() + 1),
            "total": total,
            "category_avgs": {cat: _clean(round(float(r.get("W" + cat, 0) or 0), 2))
                              for cat in NF_CAT_ORDER},
        }

    con = sqlite3.connect(db_write.DB)
    try:
        cur = con.execute("SELECT title FROM nonfiction_books "
                          "WHERE status='currently-reading' LIMIT 1").fetchone()
        nxt = con.execute("SELECT title FROM nonfiction_books "
                          "WHERE status='reading-next' LIMIT 1").fetchone()
        last = con.execute("SELECT title FROM nonfiction_books "
                           "ORDER BY rowid DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return {
        "last_read": slot_for(last[0] if last else None),
        "currently_reading": slot_for(cur[0] if cur else None),
        "reading_next": slot_for(nxt[0] if nxt else None),
    }


class NonfictionAddRequest(BaseModel):
    title: str
    author: Optional[str] = None
    genre: Optional[str] = None
    scores: dict
    series: Optional[str] = None
    series_number: Optional[float] = None
    words: Optional[int] = None
    year_read: Optional[int] = None


@app.post("/api/nonfiction/books")
def add_nf_book(req: NonfictionAddRequest):
    """Add a rated nonfiction book via db_write.add_nonfiction_book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_nonfiction_book(
                title=req.title, author=req.author, genre=req.genre,
                scores=req.scores, series=req.series,
                series_number=req.series_number, words=req.words,
                year_read=req.year_read,
            )
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok or "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not add book.")
    _invalidate_nf_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NonfictionScoresRequest(BaseModel):
    scores: dict


@app.post("/api/nonfiction/books/{title}/scores")
def edit_nf_scores(title: str, req: NonfictionScoresRequest):
    """Update component scores on a nonfiction book (recomputes its averages)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.change_nonfiction_rating(title, req.scores)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not update scores.")
    _invalidate_nf_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.delete("/api/nonfiction/books/{title}")
def delete_nf_book(title: str):
    """Permanently delete a nonfiction book via db_write.delete_nonfiction_book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.delete_nonfiction_book(title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok or "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not delete book.")
    _invalidate_nf_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.post("/api/nonfiction/reading/set-year")
def set_nf_year(req: SetYearRequest):
    """Set year_read on a nonfiction book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_nonfiction_year_read(req.title, req.year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not set year.")
    _invalidate_nf_engine()
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NonfictionResearchRequest(BaseModel):
    title: str
    author: str
    genre: Optional[str] = None


@app.post("/api/nonfiction/predict/research")
def predict_nf_research(req: NonfictionResearchRequest):
    """Grounded nonfiction prediction: one LLM call scores the 8 components, then
    they roll up through the SAME nonfiction math (category averages, Quality-lean
    WA, Total Average) and are ranked by Total Average against the rated nonfiction
    books. Always low-confidence at n=6. No TBR save (there is no nonfiction
    recommendations table)."""
    if _nr is None:
        raise HTTPException(status_code=500, detail="nonfiction_research not available")
    try:
        data = _get_nf_engine()
        r = _nr.research_and_predict(req.title, req.author, req.genre or "Nonfiction", data=data)
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Research failed: {e}")
    cat_components = data[0].attrs["category_components"]
    grouped = {
        cat: {c: _clean(round(float(r["scores"][c]), 2))
              for c in cat_components.get(cat, []) if c in r["scores"]}
        for cat in NF_CAT_ORDER
    }
    return {
        "title": r["title"], "author": r["author"], "genre": "Nonfiction",
        "components": grouped,
        "category_avgs": {k: _clean(round(float(v), 2)) for k, v in r["cat_avgs"].items()},
        "wa": _clean(round(float(r["wa"]), 4)),
        "total_average": _clean(round(float(r["total_average"]), 4)),
        "rank": r["rank"], "total": r["n"],
        "confidence": r["confidence"], "low_confidence": True,
        "category_order": list(NF_CAT_ORDER),
    }


class NonfictionDiscoverRequest(BaseModel):
    request: str
    n: Optional[int] = None


@app.post("/api/nonfiction/discover/candidates")
def discover_nf_candidates(req: NonfictionDiscoverRequest):
    """Brainstorm nonfiction candidates for a free-text request (one cheap Sonnet
    call), excluding books already in your nonfiction library or TBR."""
    if _nr is None:
        raise HTTPException(status_code=500, detail="nonfiction_research not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Client error: {e}")
    request = (req.request or "").strip()
    if not request:
        raise HTTPException(status_code=422, detail="Enter a request.")
    try:
        cands = _nr.discover_nonfiction_candidates(request, n=req.n or 8, client=client)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Candidate generation failed: {e}")
    con = sqlite3.connect(db_write.DB)
    have = {r[0].strip().lower() for r in con.execute("SELECT title FROM nonfiction_books") if r[0]}
    have |= {r[0].strip().lower() for r in con.execute("SELECT title FROM nonfiction_recommendations") if r[0]}
    con.close()
    fresh = [c for c in cands if c["title"].strip().lower() not in have]
    note = "" if fresh else "Every suggestion is already in your library or TBR — try a different request."
    return {"candidates": fresh, "request": request, "note": note}


# ─── Nonfiction TBR (recommendations + read queue) ───────────────────────────

@app.get("/api/nonfiction/read-queue")
def get_nf_read_queue():
    """Not-done nonfiction recommendations with components, category averages,
    Total Average / WA (computed on read), and predicted rank by Total Average."""
    books, gw, gcw = _get_nf_engine()
    bt = nfe.add_total_average(books)
    rated_ta = bt["Total Average"].values

    COMPONENTS = db_write.NONFICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = sqlite3.connect(db_write.DB)
    rows = con.execute(
        f'SELECT title, author, genre, series, series_number, words, blurb, keywords, {comp_cols} '
        f'FROM nonfiction_recommendations WHERE done=0'
    ).fetchall()
    con.close()

    result = []
    for r in rows:
        title, author, genre, series, series_number, words, blurb, keywords = r[:8]
        comp_vals = dict(zip(COMPONENTS, r[8:]))
        wa, cat_avgs = nfe.wa_from_components(comp_vals, genre or "Nonfiction", gw, gcw)
        present = [v for v in cat_avgs.values() if v == v]
        total = sum(present) / len(present) if present else float("nan")
        result.append({
            "title": (title or "").strip(),
            "author": (author or "").strip(),
            "genre": genre or "Nonfiction",
            "series": (series or "").strip().strip("'\""),
            "series_number": _norm_snum(series_number),
            "words": words,
            "blurb": blurb or "",
            "keywords": keywords or "",
            "components": {c: _clean(float(v)) if v is not None else None
                           for c, v in comp_vals.items()},
            "category_avgs": {k: _clean(round(float(v), 4)) for k, v in cat_avgs.items()},
            "wa": _clean(round(float(wa), 4)) if wa == wa else None,
            "total_average": _clean(round(float(total), 4)) if total == total else None,
            "predicted_rank": int((rated_ta > total).sum() + 1) if total == total else None,
        })
    result.sort(key=lambda b: (b["total_average"] is not None, b["total_average"] or 0.0),
                reverse=True)
    return {"recommendations": result, "genres": []}


class NonfictionRecRequest(BaseModel):
    title: str
    author: Optional[str] = None
    genre: Optional[str] = None
    scores: dict
    series: Optional[str] = None
    series_number: Optional[float] = None
    words: Optional[int] = None
    blurb: Optional[str] = None
    keywords: Optional[str] = None


@app.post("/api/nonfiction/recommendations")
def add_nf_recommendation(req: NonfictionRecRequest):
    """Save a researched nonfiction book to the TBR."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_nonfiction_recommendation(
                title=req.title, author=req.author, genre=req.genre,
                scores=req.scores, series=req.series,
                series_number=req.series_number, words=req.words,
                blurb=req.blurb, keywords=req.keywords)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok or "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not save.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.delete("/api/nonfiction/recommendations/{title}")
def delete_nf_recommendation(title: str):
    """Remove a nonfiction TBR recommendation."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.delete_nonfiction_recommendation(title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not delete.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NfDoneRequest(BaseModel):
    done: bool = True


@app.post("/api/nonfiction/recommendations/{title}/done")
def set_nf_done(title: str, req: NfDoneRequest):
    """Mark a nonfiction recommendation done / not-done."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_nonfiction_done(title, req.done)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not update.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.get("/api/nonfiction/queue")
def get_nf_queue():
    """Ordered nonfiction read-queue titles."""
    con = sqlite3.connect(db_write.DB)
    titles = [r[0] for r in con.execute(
        "SELECT title FROM nonfiction_read_queue ORDER BY position")]
    con.close()
    return {"titles": titles}


@app.post("/api/nonfiction/queue")
def update_nf_queue(req: UpdateQueueRequest):
    """Replace the nonfiction read queue with the given ordered titles."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.update_nonfiction_queue(req.titles)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "message": buf.getvalue().strip().replace("✓", "").strip()}


@app.get("/api/stats")
def get_combined_stats():
    """Combined Fiction + Nonfiction stats. The two WAs come from different
    formulas, so the cross-type ranking is by TOTAL AVERAGE (the unweighted mean
    of category averages — directly comparable across types on the same 0-10
    scale). WA is shown only inside each type. Tier distributions are reported
    per type (fiction banded by WA, nonfiction by Total Average) since the bases
    differ. Reuses the fiction + nonfiction engines; computes no new math."""
    fbt = views_mod.add_total_average(_get_engine()[0])
    nbt = nfe.add_total_average(_get_nf_engine()[0])

    def _summ(bt):
        words = bt["Words"].dropna() if "Words" in bt else []
        return {
            "books": int(len(bt)),
            "avg_wa": _clean(round(float(bt["WA"].mean()), 2)) if len(bt) else None,
            "avg_total_average": _clean(round(float(bt["Total Average"].mean()), 2)) if len(bt) else None,
            "total_words": int(words.sum()) if len(words) else 0,
        }

    f_sum, n_sum = _summ(fbt), _summ(nbt)

    def _rows(bt, kind):
        out = []
        for _, r in bt.iterrows():
            wa = r.get("WA")
            out.append({
                "title": r["Book"], "author": str(r["Author"]),
                "genre": str(r["Genre"]), "type": kind,
                "total_average": _clean(round(float(r["Total Average"]), 4))
                if r["Total Average"] == r["Total Average"] else None,
                "wa": _clean(round(float(wa), 4)) if wa is not None and wa == wa else None,
            })
        return out

    combined = [b for b in (_rows(fbt, "fiction") + _rows(nbt, "nonfiction"))
                if b["total_average"] is not None]
    combined.sort(key=lambda b: b["total_average"], reverse=True)
    for i, b in enumerate(combined):
        b["rank"] = i + 1

    f_tiers = views_mod.tier_counts(views_mod.tier_bands(fbt, "WA", 9.5)) if len(fbt) else {}
    n_tiers = nfe.tier_counts(nfe.tier_bands(nbt, "Total Average", 9.5)) if len(nbt) else {}

    years: dict = {}
    for bt, key in ((fbt, "fiction"), (nbt, "nonfiction")):
        for _, r in bt.iterrows():
            y = r.get("Year")
            if y is None or y != y:
                continue
            years.setdefault(int(y), {"fiction": 0, "nonfiction": 0})[key] += 1
    per_year = [{"year": y, "fiction": v["fiction"], "nonfiction": v["nonfiction"],
                 "books": v["fiction"] + v["nonfiction"]}
                for y, v in sorted(years.items())]

    all_ta = ([float(x) for x in fbt["Total Average"] if x == x]
              + [float(x) for x in nbt["Total Average"] if x == x])
    return {
        "totals": {
            "total_books": f_sum["books"] + n_sum["books"],
            "fiction_books": f_sum["books"],
            "nonfiction_books": n_sum["books"],
            "total_words": f_sum["total_words"] + n_sum["total_words"],
            "avg_total_average": round(sum(all_ta) / len(all_ta), 2) if all_ta else None,
        },
        "by_type": {"fiction": f_sum, "nonfiction": n_sum},
        "tier_distribution": {
            "tier_order": views_mod.TIER_ORDER,
            "fiction": f_tiers,
            "nonfiction": n_tiers,
        },
        "per_year": per_year,
        "combined_ranking": combined,
    }


def _enrich_recommendation(req: "SaveRecommendationRequest"):
    """Generate the rich house-style blurb and resolve series + ordinal at SAVE
    time (deferred from scoring so the two extra LLM calls are only paid for
    books actually kept). Best-effort: returns (blurb, series, series_number),
    falling back to whatever the request already carried if the LLM is
    unavailable or the calls fail."""
    blurb = req.blurb or None
    series = req.series or None
    series_number = req.series_number or None

    if _rp is None:
        return blurb, series, series_number
    try:
        client = _rp.get_client()
    except Exception:
        return blurb, series, series_number  # no key → keep what was passed

    # Series + ordinal via the shared meta-prompt path.
    try:
        meta = _lookup_series_meta(client, req.title, req.author)
        if meta["series"]:
            series = meta["series"]
            series_number = meta["series_number"]
    except Exception:
        pass

    # Rich blurb from the corrected scores + the reader's own library. Needs the
    # engine for WA/CI, grounding counts, and the analog source.
    if req.scores:
        try:
            books_e, gw_e, gcw_e, _coeffs, _r2, resid_sd, _ginfo, _up = _get_engine()
            genre = req.genre
            wa = 0.0
            for cat in db_loader.CATEGORY_OF_INTEREST:
                wcat = db_loader._weighted_cat_avg(req.scores, genre, cat, gcw_e)
                wa += wcat * ((gw_e.get(genre, {}) or {}).get(cat, 0) or 0)
            half = 1.645 * resid_sd
            ci = (wa - half, wa + half)
            n_genre = int((books_e["Genre"] == genre).sum())
            n_author = int((books_e["Author"] == req.author).sum())
            read_books = [
                (str(r["Book"]), str(r["Author"]), str(r["Genre"]))
                for _, r in books_e.iterrows()
            ]
            rich = _rp.generate_rich_blurb(
                client, req.title, req.author, genre,
                req.scores, wa, ci, n_genre, n_author, read_books,
            )
            if rich:
                blurb = rich
        except Exception:
            pass

    return blurb, series, series_number


@app.post("/api/recommendations")
def save_recommendation(req: SaveRecommendationRequest):
    """Save a researched book to recommendations (TBR list). Generates the rich
    blurb and resolves series/ordinal here (deferred from scoring) so those LLM
    calls are only spent on books the reader keeps."""
    blurb, series, series_number = _enrich_recommendation(req)

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_recommendation(
                req.title, req.genre, req.author, req.scores,
                series=series,
                series_number=series_number,
                words=req.words or None,
                blurb=blurb,
                keywords=req.keywords or None,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not save recommendation.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


# ─────────────────────────────────────────────────────────────────────────────
# DELTA LOG
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# CALIBRATION — model-health (free) and LOO accuracy (slow, on-demand)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/calibration/health")
def get_calibration_health():
    """
    Free model-health metrics from the cached engine build:
    R², residual SD, regression coefficients, and per-genre bias/trust.
    """
    books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream = _get_engine()
    return {
        "n_books": len(books),
        "r2": round(float(r2), 4),
        "resid_sd": round(float(resid_sd), 4),
        "coeffs": {
            "intercept": round(float(coeffs[0]), 4),
            "story":     round(float(coeffs[1]), 4),
            "character": round(float(coeffs[2]), 4),
            "aesthetics":round(float(coeffs[3]), 4),
            "theme":     round(float(coeffs[4]), 4),
        },
        "genre_info": {
            g: {
                "bias":  round(float(v["bias"]), 4),
                "n":     int(v["n"]),
                "trust": round(float(v["trust"]), 4),
            }
            for g, v in sorted(ginfo.items())
        },
    }


@app.post("/api/calibration/loo")
def run_loo_validation():
    """
    Honest leave-one-out validation. Refits the engine ~n times — SLOW (seconds).
    Triggered explicitly by the user on the Calibration page, not on every load.
    """
    books, gw, gcw = _get_engine()[:3]
    try:
        result = ve.run_loo(books=books, gw=gw, gcw=gcw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LOO validation failed: {e}")
    return result


@app.get("/api/calibration/researcher-comparison")
def get_researcher_comparison():
    """Serve the last memory-vs-web-grounded per-component MAE comparison, if one
    has been run. This reads the static output of compare_researchers.py — a
    measurement artifact, NOT a live metric — so it never triggers LLM spend or
    touches the engine. Returns 404 when the comparison hasn't been run yet."""
    path = os.path.join(PROJECT_ROOT, "compare_researchers_result.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No researcher comparison run yet.")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Could not read comparison: {e}")


@app.get("/api/delta-log")
def get_delta_log():
    """Return all recorded prediction-vs-actual deltas, newest first."""
    COMPS = db_write.FICTION_COMPONENTS

    def _col(c: str) -> str:
        return c.replace(" ", "_").replace("-", "_")

    pred_cols = [f'"pred_{_col(c)}" as "pred_{_col(c)}"' for c in COMPS]
    act_cols  = [f'"act_{_col(c)}"  as "act_{_col(c)}"'  for c in COMPS]
    d_cols    = [f'"d_{_col(c)}"    as "d_{_col(c)}"'    for c in COMPS]
    sel = ", ".join(
        ["id", "title", "logged_at", "pred_wa", "act_wa", "d_wa"]
        + pred_cols + act_cols + d_cols
    )
    con = sqlite3.connect(db_write.DB)
    rows = con.execute(
        f"SELECT {sel} FROM delta_log ORDER BY id DESC"
    ).fetchall()
    col_names = (
        ["id", "title", "logged_at", "pred_wa", "act_wa", "d_wa"]
        + [f"pred_{_col(c)}" for c in COMPS]
        + [f"act_{_col(c)}"  for c in COMPS]
        + [f"d_{_col(c)}"    for c in COMPS]
    )
    con.close()

    entries = [dict(zip(col_names, r)) for r in rows]

    # Per-component mean delta across all logged entries (predictive drift)
    drift: dict = {}
    for c in COMPS:
        vals = [e[f"d_{_col(c)}"] for e in entries if e.get(f"d_{_col(c)}") is not None]
        drift[c] = round(sum(vals) / len(vals), 4) if vals else None

    return {
        "entries": entries,
        "components": COMPS,
        "drift": drift,
    }
