"""
backend/main.py — FastAPI wrapper around the existing Python engine.
Run from the project root: uvicorn backend.main:app --reload --port 8000
The engine modules (db_loader, db_write, predict_engine) must be importable,
which they are when you run from the BookRankingAPP directory.
"""

import sys
import os
import math
import io
import contextlib
import json
import re
import sqlite3

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

app = FastAPI(title="Reading Ledger API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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


@app.get("/api/books")
def get_books():
    """Return all rated books with their WA, metadata, and component scores."""
    books, gw, gcw = db_loader.load_from_db()
    category_components = books.attrs["category_components"]

    # Convert to a list of dicts that JSON can handle cleanly
    result = []
    for _, row in books.iterrows():
        book = {
            "title": row["Book"],
            "author": row["Author"],
            "genre": row["Genre"],
            "series": row.get("Series") or "",
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
    books, _, _ = db_loader.load_from_db()
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
    books, _, _ = db_loader.load_from_db()
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
    words: Optional[int] = None
    year_read: Optional[int] = None


@app.post("/api/books")
def add_book(req: AddBookRequest):
    """Add a newly-rated book via db_write.add_book."""
    buf = io.StringIO()
    error_msg = None
    try:
        with contextlib.redirect_stdout(buf):
            db_write.add_book(
                req.title, req.genre, req.author, req.scores,
                series=req.series or None,
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
    return {"ok": True, "message": out.replace("✓", "").strip()}


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
    try:
        import research_predict as rp
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"research_predict not available: {e}")

    try:
        client = rp.get_client()
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
            rp.research_rich_plus(
                client, title, hint_author, None,
                allowed_genres=allowed_genres,
            )

        meta_prompt = (
            f'Return ONLY a JSON object with these keys:\n'
            f'  "author": the correct full author name for "{title}"\n'
            f'  "series": the series name if the book belongs to one (empty string if standalone)\n'
            f'  "series_number": the number within the series as an integer (0 if standalone or unknown)\n'
            f'Respond with raw JSON only, no markdown.'
        )
        meta_msg = client.messages.create(
            model=rp.rm.MODEL, max_tokens=200,
            messages=[{"role": "user", "content": meta_prompt}]
        )
        meta_text = meta_msg.content[0].text.strip()
        meta_text = re.sub(r"^```(json)?|```$", "", meta_text, flags=re.MULTILINE).strip()
        meta = json.loads(meta_text)

        author = meta.get("author", hint_author).strip() or hint_author
        s_name = meta.get("series", "").strip()
        s_num = meta.get("series_number", 0) or 0
        if s_name and int(s_num) > 0:
            series = f"{s_name} #{int(s_num)}"
        else:
            series = s_name

        return {
            "title": title,
            "author": author,
            "genre": det_genre,
            "words": words_raw,
            "series": series,
            "blurb": blurb or "",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Look-up failed: {e}")


@app.get("/api/tiers")
def get_tiers(year: Optional[int] = None):
    """Return books with tier assignments (S+/S/A/B/C/D/F), optionally filtered by year_read."""
    books, _, _ = db_loader.load_from_db()
    category_components = books.attrs["category_components"]

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


@app.get("/api/read-queue")
def get_read_queue():
    """Return all not-done recommendations with flat component scores and predicted rank."""
    books, gw, gcw = db_loader.load_from_db()
    rated_wa = books["WA"].values

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = sqlite3.connect(db_write.DB)
    rows = con.execute(
        f'SELECT title, author, genre, series, words, blurb, keywords, {comp_cols} '
        f'FROM recommendations WHERE done=0'
    ).fetchall()
    con.close()

    result = []
    for r in rows:
        title, author, genre, series, words, blurb, keywords = r[:7]
        comp_vals = dict(zip(COMPONENTS, r[7:]))

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
        data = pe.build(source="db")
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
        "r2": round(p["r2"], 4),
    }


class ResearchRequest(BaseModel):
    title: str
    author: str
    genre: Optional[str] = None   # None → auto-detect from the LLM


@app.post("/api/predict/research")
def predict_research(req: ResearchRequest):
    """
    Grounded research prediction: research_rich_plus → correlation-smooth →
    author+genre correct → WA roll-up. One LLM API call (or cache hit).
    Returns corrected components, WA, CI, rank, grounding signals.
    """
    try:
        import research_predict as rp
    except ImportError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        client = rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Client error: {e}")

    try:
        data = pe.build(source="db")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine build failed: {e}")
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data

    con = sqlite3.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()

    cache = rp.load_cache()
    try:
        scores, conf, blurb, keywords, det_genre, words, from_cache = rp.research_book(
            req.title, req.author, req.genre, client, cache,
            allowed_genres=allowed_genres,
        )
        rp.save_cache(cache)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Research failed: {e}")

    eff_genre = req.genre or det_genre
    if eff_genre is None:
        raise HTTPException(status_code=422,
                            detail="Could not auto-detect a genre — pick one manually.")

    try:
        corr_models = rp.build_corr_models(books_e, cache)
        res = rp.correct_and_predict(
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

    return {
        "title": res["title"], "author": res["author"], "genre": res["genre"],
        "wa": round(res["wa"], 4),
        "ci": [round(res["ci"][0], 4), round(res["ci"][1], 4)],
        "rank": res["rank"], "total": res["total"],
        "n_genre": res["n_genre"], "n_author": res["n_author"],
        "conf": res["conf"],
        "from_cache": from_cache,
        "words": words,
        "blurb": res.get("blurb", ""),
        "keywords": res.get("keywords", ""),
        "components": components_by_cat,
        "category_order": list(cat_comps.keys()),
        "genre_auto_detected": req.genre is None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER — generate candidates then score them individually
# ─────────────────────────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    request: str
    max_candidates: int = 8


@app.post("/api/discover/candidates")
def discover_candidates(req: DiscoverRequest):
    """Generate candidate book titles for a free-text request (1 API call)."""
    try:
        import research_predict as rp
    except ImportError as e:
        raise HTTPException(status_code=500, detail=str(e))
    try:
        client = rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")

    books, _, _ = db_loader.load_from_db()
    cache = rp.load_cache()

    con = sqlite3.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    tbr_books = [(t or "", a or "") for t, a in con.execute(
        "SELECT title, author FROM recommendations")]
    con.close()

    read_books = list(zip(books["Book"].tolist(), books["Author"].tolist()))

    try:
        candidates = rp.generate_candidates(
            req.request.strip(), allowed_genres, read_books,
            tbr_books=tbr_books, n=req.max_candidates, client=client,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Candidate generation failed: {e}")

    # Flag which are already in cache (free to score)
    for c in candidates:
        c["cached"] = c.get("title", "") in cache

    return {"candidates": candidates, "request": req.request.strip()}


class SaveRecommendationRequest(BaseModel):
    title: str
    genre: str
    author: str
    scores: dict[str, float]
    words: Optional[int] = None
    blurb: Optional[str] = None
    keywords: Optional[str] = None
    series: Optional[str] = None


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
    try:
        import research_predict as rp
    except ImportError as e:
        raise HTTPException(status_code=500, detail=str(e))
    try:
        client = rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Client error: {e}")
    try:
        blurb, keywords = rp.generate_blurb_keywords(req.title, req.author, req.genre, client)
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
    books, _, _ = db_loader.load_from_db()
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
    """Unread pool (TBR + queue) and recently-finished rated books."""
    books, _, _ = db_loader.load_from_db()
    rated_wa = books["WA"].values

    # Unread pool: recommendations (done=0) + read_queue
    con = sqlite3.connect(db_write.DB)
    pool = {}
    for title, author, genre in con.execute(
            "SELECT title,author,genre FROM recommendations WHERE done=0"):
        t = (title or "").strip()
        if t:
            pool[t] = {"author": (author or "").strip(), "genre": (genre or "").strip()}
    for (title,) in con.execute("SELECT title FROM read_queue ORDER BY position"):
        t = (title or "").strip()
        if t and t not in pool:
            pool[t] = {"author": "", "genre": ""}
    con.close()

    # Recently finished = rated books with year_read
    finished_books = []
    finished = books[books["Status"] == "finished"].dropna(subset=["Year"])
    if not finished.empty:
        last_year = int(finished["Year"].max())
        last_read = finished[finished["Year"] == last_year].sort_values("WA", ascending=False)
        total_rated = len(books)
        for _, row in last_read.iterrows():
            rank = int((rated_wa > float(row["WA"])).sum() + 1)
            finished_books.append({
                "title": row["Book"],
                "author": row["Author"],
                "genre": row["Genre"],
                "year": int(row["Year"]) if row["Year"] == row["Year"] else None,
                "wa": round(float(row["WA"]), 2),
                "rank": rank,
                "total": total_rated,
            })
        last_year_val = last_year
    else:
        last_year_val = None

    return {
        "pool": [{"title": t, "author": m["author"], "genre": m["genre"]}
                 for t, m in sorted(pool.items())],
        "last_year": last_year_val,
        "finished": finished_books,
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
    return {"ok": True, "message": out.replace("✓", "").strip()}


# ─────────────────────────────────────────────────────────────────────────────
# SERIES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/series")
def get_series():
    """Series rankings: per-series aggregates sorted by Adjusted WA."""
    books, _, _ = db_loader.load_from_db()
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
    books, _, _ = db_loader.load_from_db()
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
    books, _, _ = db_loader.load_from_db()
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


@app.post("/api/recommendations")
def save_recommendation(req: SaveRecommendationRequest):
    """Save a researched book to recommendations (TBR list)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_recommendation(
                req.title, req.genre, req.author, req.scores,
                series=req.series or None,
                words=req.words or None,
                blurb=req.blurb or None,
                keywords=req.keywords or None,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not save recommendation.")
    return {"ok": True, "message": out.replace("✓", "").strip()}
