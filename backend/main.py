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
            "wa": round(float(row["WA"]), 4),
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


@app.get("/health")
def health():
    return {"ok": True}
