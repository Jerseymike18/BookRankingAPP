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
import datetime
import db_backend
import uuid
import threading
from contextlib import asynccontextmanager

# Make the project root importable regardless of where uvicorn is launched from
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)  # books.db is resolved relative to cwd

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

import pandas as pd
import db_loader
import db_write
import user_weights
import predict_engine as pe
import views as views_mod
import validate_engine as ve
import nonfiction_engine as nfe
import auth
import signup as signup_mod
import track_record as tr
import engine_parameters as ep
import delta_log_view
import timeline_month

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

# repredict_on_add pulls in the LLM research path (rp + hybrid); guarded the same
# way so the server still starts when those deps are absent (feature just no-ops).
try:
    import repredict_on_add as _repred
except Exception:
    _repred = None

# Serializes background cohort re-predictions so overlapping adds never contend on
# the SQLite writer (localhost single-user, so contention is rare, but cheap to be
# safe). The work runs off the request thread; the add-book response returns first.
_repred_lock = threading.Lock()

# Hybrid per-component sourcing (data-driven policy). Separately guarded so a
# failure here never disables the core research path; predict falls back to
# pure-memory scores if it is unavailable or disabled.
try:
    import hybrid_researcher as _hybrid
except Exception:
    _hybrid = None


# ─────────────────────────────────────────────────────────────────────────────
# CONFORMAL PREDICTION INTERVALS  (additive — never changes a point prediction)
# ─────────────────────────────────────────────────────────────────────────────
# calibration/residuals.json is a precomputed OFFLINE snapshot of leave-one-out
# residuals bucketed by data density, built by:
#     python3 validate_engine.py --write-residuals
# We load it ONCE at import (never per request — LOO refits ~127 times) and use
# it to attach an 80% interval to /api/predict/instant. If the file is missing,
# the interval fields are simply omitted; a width is never invented. If the file
# was built by a different engine (hash mismatch) we warn once and mark served
# intervals "stale".
import intervals as _intervals

_RESIDUALS_PATH = os.path.join(PROJECT_ROOT, "calibration", "residuals.json")
_RESIDUALS = _intervals.load_residuals(_RESIDUALS_PATH)
_ENGINE_HASH = _intervals.engine_hash(PROJECT_ROOT)
if _RESIDUALS is not None and _RESIDUALS.get("engine_hash") != _ENGINE_HASH:
    import logging
    logging.getLogger("uvicorn.error").warning(
        "calibration/residuals.json was built by a different engine "
        "(table=%s, serving=%s); prediction intervals will be marked 'stale'. "
        "Regenerate with `python3 validate_engine.py --write-residuals`.",
        _RESIDUALS.get("engine_hash"), _ENGINE_HASH)

# Fraction of the 80% interval half-width added to the point estimate for the
# read-queue "Upside" rank. 0.45 ≈ the P76 outcome — a good result you'd beat
# ~1 in 4 (above the median P50, which is ~the point). Higher is more optimistic
# (1.0 ≈ the ~P90 ceiling — beaten only ~1 in 10, over-optimistic across a whole
# TBR). Still scaled per author-density bucket, so thin-author / frontier picks
# keep proportionally more upside. Calibrated on the researched LOO residuals
# (P76 upside offset / P80 half-width = 0.45).
UPSIDE_FRAC = 0.45


# ─────────────────────────────────────────────────────────────────────────────
# ENGINE CACHE
# ─────────────────────────────────────────────────────────────────────────────
# The engine tuple (books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream) is
# expensive to produce: it reads the DB, fits a regression, and computes genre
# bias. We build it once at startup and serve all endpoints from the cache.
# Write endpoints call _invalidate_engine() after a successful db_write so the
# next read reflects the change.

# Per-TENANT engine caches, keyed by user_id. Each user's engine is built from
# THEIR scoped books (db_loader / nonfiction_engine filter by user_id), so one
# tenant's data can never leak into another's ranking/prediction. Local single-
# user dev (AUTH_ENABLED off) uses the one DEFAULT_USER_ID key, so its behavior
# is unchanged. Endpoints pass the token-derived user_id; a missing one falls
# back to the default so not-yet-threaded call sites stay correct locally.
_engine_cache: dict = {}
_nf_engine_cache: dict = {}


def _uid(user_id):
    return user_id or db_backend.DEFAULT_USER_ID


# Cold-start prior (Phase-4 K_USER_PRIOR, v1). A tenant with too few books to fit
# a stable model of their own BORROWS the seed tenant's fitted prediction model
# (coeffs / genre-trust / upstream). Their OWN books + weights are still used for
# listing and WA ranking — WA is computed from their EFFECTIVE weights (the global
# defaults overlaid with any of their own overrides) + their own scores in
# db_loader, so rankings stay correctly per-user. This both crash-proofs the
# 0-book case (pe.fit_regression on an empty frame raises — the multi-tenant
# cold-start 500) and gives brand-new users working predictions from book #1.
# The seed is the local single-user (Michael / DEFAULT_USER_ID), who always fits
# his own model, so his behavior is byte-identical and the 0.631 gate is intact.
# TODO(phase4): replace this hard switch with smooth shrinkage toward the prior.
SEED_USER_ID = db_backend.DEFAULT_USER_ID
MIN_OWN_FIT = 15  # below this many books, borrow the seed model instead of fitting


def _shape_empty_books(books, categories):
    """A brand-new tenant's scoped load is a 0-row frame with NO columns, so the
    read-only views (which index by name: WA, Series, Year, Words, …) KeyError.
    Reindex it to the columns the loader would have produced — zero rows, right
    shape — so those views return empty naturally. Loaders/views stay untouched;
    this is caller-layer shaping only. `categories` is the per-track category
    order (fiction vs nonfiction). Preserves the .attrs the engine reads."""
    cc = books.attrs.get("category_components", {})
    allc = list(books.attrs.get("all_components", []))
    cols = (["Book", "Genre", "Author", "Series", "Words", "Year", "Status"]
            + ["W" + cat for cat in categories]
            + allc + ["WA"])
    books = books.reindex(columns=cols)
    books.attrs["category_components"] = cc
    books.attrs["all_components"] = allc
    return books


def _build_engine_for(uid) -> tuple:
    """pe.build(source='db') for ONE tenant: a user-scoped load fed to the
    read-only engine fit functions. No prediction math is reimplemented here —
    predict_engine stays tenant-agnostic and simply receives scoped data. The
    tenant's own weight overrides (if any) are overlaid on the global weights
    before the load computes WA (see user_weights). A below-threshold tenant
    borrows the seed's fitted model (see SEED_USER_ID)."""
    books, gw, gcw = db_loader.load_from_db(
        user_id=uid, weight_overrides=user_weights.load_overrides(uid))
    if len(books) == 0:
        books = _shape_empty_books(books, db_loader.CATEGORY_OF_INTEREST)
    if uid != SEED_USER_ID and len(books) < MIN_OWN_FIT:
        # Cold start: borrow the seed's fitted prediction model, keep own books.
        _, _, _, coeffs, r2, resid_sd, ginfo, upstream = _get_engine(SEED_USER_ID)
        return books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream
    coeffs, r2, resid_sd = pe.fit_regression(books)
    ginfo = pe.genre_bias_and_trust(books, coeffs)
    upstream = pe.fit_upstream(books)
    return books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream


def _get_engine(user_id=None) -> tuple:
    uid = _uid(user_id)
    cached = _engine_cache.get(uid)
    if cached is None:
        cached = _engine_cache[uid] = _build_engine_for(uid)
    return cached


def _invalidate_engine(user_id=None) -> None:
    uid = _uid(user_id)
    _engine_cache[uid] = _build_engine_for(uid)
    _cold_term_cache.pop(uid, None)          # refit the cold-start term on next read
    _engine_epoch[uid] = _engine_epoch.get(uid, 0) + 1   # stale-keys _corr_statics
    _corr_statics_cache.pop(uid, None)


# Nonfiction engine cache — the (books, gw, gcw) tuple from the SEPARATE
# nonfiction engine, per tenant. Built lazily; rebuilt after any nonfiction write.
def _load_nf(uid) -> tuple:
    books, gw, gcw = nfe.load_nonfiction_from_db(
        user_id=uid, weight_overrides=user_weights.load_overrides_nf(uid))
    if len(books) == 0:  # brand-new tenant: shape the empty frame (see fiction)
        books = _shape_empty_books(books, nfe.NONFICTION_CATEGORY_ORDER)
    return books, gw, gcw


def _get_nf_engine(user_id=None) -> tuple:
    uid = _uid(user_id)
    cached = _nf_engine_cache.get(uid)
    if cached is None:
        cached = _nf_engine_cache[uid] = _load_nf(uid)
    return cached


def _invalidate_nf_engine(user_id=None) -> None:
    uid = _uid(user_id)
    _nf_engine_cache[uid] = _load_nf(uid)


# ─────────────────────────────────────────────────────────────────────────────
# WORD-COUNT COLD-START TERM  (per-tenant, cached)
# ─────────────────────────────────────────────────────────────────────────────
# The validated word-count cold-start adjustment (experiments/cold_start_wordcount_spec.md):
# on the cold slice (a book with no same-author analog) the correction is blind to book
# length, and this reader's residual correlates with word count. We fit the term per tenant
# on their OWN library and apply it in research_predict.correct_and_predict (n_author==0
# only). Data-rich tenants get a fitted term; cold-start tenants (too few books to fit) fall
# back to their onboarding word-count preference if set, else None (term off / unchanged).
# Kill switch: COLD_START_TERM=0.
_cold_term_cache: dict = {}
COLD_START_TERM_ENABLED = os.environ.get("COLD_START_TERM", "1") != "0"
# New-user favorite-author prior (Part B): a positive WA bump on the cold slice
# (n_author==0) when the unread book's author is a stated favorite (weight 1.0) or an
# LLM-found analog of one (discounted). Sanity-calibrated on the seed (favorite-author
# lift +0.5..+1.4; first-books-by-favorites under-predicted −0.66) → a conservative base.
_author_prior_cache: dict = {}          # normalized-favorites tuple → {base, map}
_AUTHOR_OFFSET_BASE = 0.5               # WA bump for a direct favorite
_ANALOG_WEIGHT = 0.5                    # analogs get this fraction of the favorite bump


# Center for a preference-only term: log10 of a typical novel (~160k words), so a
# stated slope pivots around a mid-length book (matches the seed's fitted mu ≈ 5.2).
_PREF_LOG_MU = 5.2


def _fit_cold_term_for(uid):
    """Fit the word-count term on a tenant's OWN library. Returns coefs, or None when
    the tenant has too few books to fit (a cold-start tenant → preference fallback)."""
    try:
        books, gw, gcw = _get_engine(uid)[:3]
        cache = _rp.load_cache()
        return _rp.fit_cold_start_term(
            books, cache, gw, gcw, corr_models=_rp.build_corr_models(books, cache))
    except Exception:
        return None


def _preference_cold_term(word_count_pref):
    """A cold-start term from a NEW user's stated word-count preference (welcome page):
    a pure slope on centered log10(words), sign+magnitude from the preference in [-1, 1]
    (long-preferring → positive). None when unset/zero. Applies only to tenants too new
    to fit their own term, and only on the cold slice (n_author==0)."""
    try:
        slope = float(word_count_pref)
    except (TypeError, ValueError):
        return None
    if not slope:
        return None
    slope = max(-2.0, min(2.0, slope))              # guard absurd values
    return {"intercept": 0.0, "slopes": [slope], "mu": [_PREF_LOG_MU],
            "use_series": 0, "n": 0}


def _expand_author_prior(favs):
    """Build {base, map} from favorite author names, widened to LLM analogs (discounted).
    Favorites weight 1.0; analogs _ANALOG_WEIGHT (never downgrading a direct favorite).
    Best-effort — an LLM failure just yields favorites alone; empty input → None."""
    m = {}
    for a in favs:
        na = _rp.normalize_author(a)
        if na:
            m[na] = 1.0
    if not m:
        return None
    try:
        analogs = _rp.find_author_analogs(list(favs), _rp.get_client())
        for sims in analogs.values():
            for s in sims:
                ns = _rp.normalize_author(s)
                if ns and ns not in m:
                    m[ns] = _ANALOG_WEIGHT
    except Exception:
        pass
    return {"base": _AUTHOR_OFFSET_BASE, "map": m}


def _build_author_prior(fav_authors):
    """Cached author prior for a favorites list, keyed by the normalized-favorites tuple
    so it rebuilds when the reader changes them. None when there are no usable favorites."""
    favs = tuple(str(a).strip() for a in (fav_authors or []) if str(a).strip())[:5]
    if not favs:
        return None
    if favs not in _author_prior_cache:
        _author_prior_cache[favs] = _expand_author_prior(favs)
    return _author_prior_cache[favs]


def _get_cold_term(user_id=None, word_count_pref=None, fav_authors=None):
    """Per-tenant cold-start term — two INDEPENDENT components, each applied only on the
    cold slice (n_author==0) by correct_and_predict:
      * word count: the tenant's FITTED slope once they have enough books, else their
        onboarding word-count preference (new users);
      * author prior: favorite authors + analogs, attached whenever set. It fades PER
        AUTHOR via the n_author==0 gate (the moment you rate that author), NOT with library
        size — so a favorite you still haven't read keeps its nudge even once you're data-rich.
    None when neither component applies."""
    if not COLD_START_TERM_ENABLED or _rp is None:
        return None
    uid = _uid(user_id)
    if uid not in _cold_term_cache:
        _cold_term_cache[uid] = _fit_cold_term_for(uid)     # fitted coefs or None
    fitted = _cold_term_cache[uid]
    # Word-count component: fitted (data-rich) else the stated preference (new user).
    # dict(...) copies so attaching an author prior never mutates the cached fitted term.
    term = dict(fitted if fitted is not None
                else (_preference_cold_term(word_count_pref) or {}))
    ap = _build_author_prior(fav_authors)                   # independent of library size
    if ap:
        term["author_prior"] = ap
    return term or None                                     # {} → nothing to apply


def _cold_adjust_rec_wa(wa, words, series_number, author, n_author, cold_term):
    """Apply the cold-start term to a SAVED recommendation's displayed WA so cold-slice
    recs rank consistently with the live Predict page. No-op unless the reader has a term
    and the rec has no same-author analog (n_author == 0) — the same gate correct_and_predict
    uses. Keeps the read-queue and reading-status slots agreeing on the same book's WA."""
    if cold_term is None or n_author != 0 or _rp is None:
        return wa
    return _rp.apply_cold_start_term(wa, words, series_number, author, cold_term)


def _correction_pool(user_id, books_e):
    """Training pool for the research-path author+genre correction. A tenant with too few
    books to fit their own model would otherwise correct against a tiny/empty library —
    which is degenerate (near-raw), noisy (a handful of idiosyncratic ratings swing the
    prediction wildly), or an outright crash on an empty pool. So a below-threshold tenant
    borrows the SEED's calibrated books UNIONed with their own (their reads still add
    analogs; the seed's 129 dominate the calibration). This mirrors the model borrow in
    _build_engine_for and completes it for the research path. The seed and any data-rich
    tenant use their own books unchanged, so their predictions are byte-identical."""
    if user_id == SEED_USER_ID or len(books_e) >= MIN_OWN_FIT:
        return books_e
    seed_books = _get_engine(SEED_USER_ID)[0]
    return pd.concat([seed_books, books_e]).drop_duplicates(
        subset=["Book"], keep="last").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Per-run correction statics (latency only — no math change)
# ─────────────────────────────────────────────────────────────────────────────
# The research-predict path used to rebuild the correction-training pairs table
# and the 14 correlation-smoothing models on EVERY request (and once per book in
# bulk passes) even though their inputs only change when the tenant's library
# changes (engine invalidation) or the research cache file gains entries. Both
# are now computed once and cached per tenant, keyed by (own engine epoch, seed
# engine epoch, research-cache mtime) — the seed epoch matters because a
# cold-start tenant's correction pool borrows the seed's books. Same inputs →
# same pairs/models; a stale key just recomputes. Latency only.
_corr_statics_cache: dict = {}   # uid -> (key, pairs, corr_models)
_engine_epoch: dict = {}         # uid -> int, bumped by _invalidate_engine


def _research_cache_mtime() -> float:
    try:
        return os.path.getmtime(_rp.CACHE)
    except (OSError, AttributeError):    # missing file / _rp unavailable
        return 0.0


def _corr_statics(user_id, corr_pool):
    """(pairs, corr_models) for this tenant's correction pool, cached per engine
    epoch + research-cache mtime. `corr_pool` must be _correction_pool(...)'s
    result for this tenant — the key tracks exactly the inputs that frame is
    built from. Returns (None, None) if the build fails (callers fall back to
    per-call behavior)."""
    uid = _uid(user_id)
    key = (_engine_epoch.get(uid, 0), _engine_epoch.get(SEED_USER_ID, 0),
           _research_cache_mtime())
    hit = _corr_statics_cache.get(uid)
    if hit is not None and hit[0] == key:
        return hit[1], hit[2]
    try:
        cache = _rp.load_cache()
        pairs = _rp.rm.build_pairs(corr_pool, cache)
        corr_models = _rp.build_corr_models(corr_pool, cache, pairs=pairs)
    except Exception:
        return None, None
    _corr_statics_cache[uid] = (key, pairs, corr_models)
    return pairs, corr_models


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


def _read_month_map(user_id, table):
    """{normalized-title: read_month(1-12)} for one tenant + table, for the
    by-month Timeline. Reads directly (a read, not a write); titles with a NULL
    read_month are omitted. `table` is a trusted internal literal
    ('books' | 'nonfiction_books'), never user input."""
    con = db_backend.connect(db_write.DB)
    rows = con.execute(
        f"SELECT title, read_month FROM {table} WHERE user_id=?", (user_id,)
    ).fetchall()
    con.close()
    return {
        (t or "").strip().lower(): int(m)
        for (t, m) in rows if m is not None
    }


def _norm_snum(num):
    """Normalize a stored series_number: None stays None, whole values become
    int (so JSON shows 6 not 6.0), fractional values (0.5, 3.5) stay float."""
    if num is None:
        return None
    return int(num) if float(num) == int(num) else float(num)


def _series_number_map(table: str, user_id: str) -> dict:
    """Return {lowercased-title: series_number} for a table. Used to attach
    ordinals to engine-backed responses (db_loader is read-only and doesn't
    carry series_number). series_number may be int or float (0.5 prequels).
    Tenant-scoped: only the caller's own rows contribute ordinals."""
    con = db_backend.connect(db_write.DB)
    try:
        rows = con.execute(
            f"SELECT title, series_number FROM {table} "
            f"WHERE series_number IS NOT NULL AND user_id=?",
            (user_id,)
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
def get_books(user_id: str = Depends(auth.get_current_user_id)):
    """Return all rated books with their WA, metadata, and component scores."""
    books, gw, gcw = _get_engine(user_id)[:3]
    category_components = books.attrs["category_components"]
    snum_map = _series_number_map("books", user_id)

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
def get_valid_genres(user_id: str = Depends(auth.get_current_user_id)):
    """Genres valid for adding a book: the global genre_weights set PLUS the
    caller's own private genres."""
    con = db_backend.connect(db_write.DB)
    genres = {r[0] for r in con.execute("SELECT genre FROM genre_weights")}
    genres |= {r[0] for r in con.execute(
        "SELECT DISTINCT genre FROM genre_weight_overrides WHERE user_id=?", (user_id,))}
    con.close()
    return sorted(genres)


# ─────────────────────────────────────────────────────────────────────────────
# GENRE / COMPONENT WEIGHTS  (per-tenant tailoring)
# ─────────────────────────────────────────────────────────────────────────────
# The weights that turn component scores into WA. Global tables are the shared
# default; each tenant may override them (stored sparsely by db_write, overlaid
# in db_loader). Every write normalizes to sum 1.0 and rebuilds the caller's
# engine, so their rankings/predictions re-order immediately. Auth-scoped.
class GenreWeightsRequest(BaseModel):
    weights: dict[str, float]          # the 5 categories -> weight


class ComponentWeightsRequest(BaseModel):
    weights: dict[str, float]          # one (genre, category)'s components -> weight


class ResetWeightsRequest(BaseModel):
    genre: Optional[str] = None        # None -> reset everything for the user
    category: Optional[str] = None     # with genre -> reset just that component split


class AddGenreRequest(BaseModel):
    name: str                          # the new private genre's name
    weights: dict[str, float]          # its category weights (normalized server-side)


@app.get("/api/weights")
def get_weights(user_id: str = Depends(auth.get_current_user_id)):
    """The caller's EFFECTIVE genre + component weights — global defaults overlaid
    with their own overrides, plus per-group `customized` flags — for the weights
    editor. Read-only."""
    return user_weights.effective_weights(user_id)


@app.put("/api/weights/genre/{genre}")
def put_genre_weights(genre: str, req: GenreWeightsRequest,
                      user_id: str = Depends(auth.get_current_user_id)):
    """Override the 5 category weights (Story/Character/Theme/Aesthetics/
    Worldbuilding) for one genre, for the caller. Normalized to sum 1.0."""
    if not db_write.set_genre_weights(genre, req.weights, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not set category weights for '{genre}'. "
                   "Provide the 5 categories as non-negative numbers.")
    _invalidate_engine(user_id)
    return {"ok": True}


@app.put("/api/weights/component/{genre}/{category}")
def put_component_weights(genre: str, category: str, req: ComponentWeightsRequest,
                          user_id: str = Depends(auth.get_current_user_id)):
    """Override the within-category component weights for one (genre, category),
    for the caller. Must supply exactly that group's components; normalized to
    sum 1.0."""
    if not db_write.set_component_weights(genre, category, req.weights,
                                          user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not set component weights for '{genre}' / {category}. "
                   "Supply exactly that category's components as non-negative numbers.")
    _invalidate_engine(user_id)
    return {"ok": True}


@app.post("/api/weights/reset")
def post_reset_weights(req: ResetWeightsRequest,
                       user_id: str = Depends(auth.get_current_user_id)):
    """Revert the caller's weight overrides to the global defaults. Scope: whole
    account (no body), one genre (`genre`), or one component split (`genre` +
    `category`)."""
    if not db_write.reset_weights(user_id=user_id, genre=req.genre,
                                  category=req.category):
        raise HTTPException(status_code=422, detail="Could not reset weights.")
    _invalidate_engine(user_id)
    return {"ok": True}


@app.post("/api/weights/genre")
def post_add_genre(req: AddGenreRequest,
                   user_id: str = Depends(auth.get_current_user_id)):
    """Create a PRIVATE fiction genre for the caller (category weights + equal
    component seeds). It becomes selectable when adding books and rankable."""
    if not db_write.add_genre(req.name, req.weights, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not add genre “{req.name}”. It may already exist, or the "
                   "category weights are missing/invalid.")
    _invalidate_engine(user_id)
    return {"ok": True}


@app.delete("/api/weights/genre/{genre}")
def delete_genre(genre: str, user_id: str = Depends(auth.get_current_user_id)):
    """Delete one of the caller's PRIVATE fiction genres. Refused for global
    genres or if any of the caller's books still use it."""
    if not db_write.delete_user_genre(genre, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not delete “{genre}”. It must be one of your own genres "
                   "with no books or predictions assigned to it.")
    _invalidate_engine(user_id)
    return {"ok": True}


# ── Nonfiction weights (same shape, separate track / engine) ──────────────────
@app.get("/api/nonfiction/weights")
def get_nonfiction_weights(user_id: str = Depends(auth.get_current_user_id)):
    """The caller's effective nonfiction genre + component weights (Quality/
    Aesthetics/Theme), for the weights editor. Read-only."""
    return user_weights.effective_weights_nf(user_id)


@app.put("/api/nonfiction/weights/genre/{genre}")
def put_nonfiction_genre_weights(genre: str, req: GenreWeightsRequest,
                                 user_id: str = Depends(auth.get_current_user_id)):
    """Override the nonfiction category weights for one genre. Normalized to 1.0."""
    if not db_write.set_nonfiction_genre_weights(genre, req.weights, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not set nonfiction category weights for '{genre}'. "
                   "Provide Quality/Aesthetics/Theme as non-negative numbers.")
    _invalidate_nf_engine(user_id)
    return {"ok": True}


@app.put("/api/nonfiction/weights/component/{genre}/{category}")
def put_nonfiction_component_weights(genre: str, category: str,
                                     req: ComponentWeightsRequest,
                                     user_id: str = Depends(auth.get_current_user_id)):
    """Override the within-category nonfiction component weights for one
    (genre, category). Must supply exactly that group's components; normalized."""
    if not db_write.set_nonfiction_component_weights(genre, category, req.weights,
                                                     user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not set nonfiction component weights for '{genre}' / {category}. "
                   "Supply exactly that category's components as non-negative numbers.")
    _invalidate_nf_engine(user_id)
    return {"ok": True}


@app.post("/api/nonfiction/weights/reset")
def post_reset_nonfiction_weights(req: ResetWeightsRequest,
                                  user_id: str = Depends(auth.get_current_user_id)):
    """Revert the caller's nonfiction weight overrides to the global defaults."""
    if not db_write.reset_nonfiction_weights(user_id=user_id, genre=req.genre,
                                             category=req.category):
        raise HTTPException(status_code=422, detail="Could not reset nonfiction weights.")
    _invalidate_nf_engine(user_id)
    return {"ok": True}


@app.post("/api/nonfiction/weights/genre")
def post_add_nonfiction_genre(req: AddGenreRequest,
                              user_id: str = Depends(auth.get_current_user_id)):
    """Create a PRIVATE nonfiction genre for the caller."""
    if not db_write.add_nonfiction_genre(req.name, req.weights, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not add nonfiction genre “{req.name}”. It may already exist, "
                   "or the category weights are missing/invalid.")
    _invalidate_nf_engine(user_id)
    return {"ok": True}


@app.delete("/api/nonfiction/weights/genre/{genre}")
def delete_nonfiction_genre(genre: str,
                            user_id: str = Depends(auth.get_current_user_id)):
    """Delete one of the caller's PRIVATE nonfiction genres."""
    if not db_write.delete_nonfiction_user_genre(genre, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail=f"Could not delete nonfiction genre “{genre}”. It must be one of your "
                   "own genres with no books or predictions assigned to it.")
    _invalidate_nf_engine(user_id)
    return {"ok": True}


@app.get("/api/books/{title}/scores")
def get_book_scores(title: str, user_id: str = Depends(auth.get_current_user_id)):
    """Return component scores for a single rated book (for Edit Ratings)."""
    books = _get_engine(user_id)[0]
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
    read_month: Optional[int] = None  # 1-12; defaults to the current month


@app.post("/api/books")
def add_book(req: AddBookRequest, background_tasks: BackgroundTasks,
             user_id: str = Depends(auth.get_current_user_id)):
    """Add a newly-rated book via db_write.add_book, then dequeue it."""
    # Default the read month to "now" so a freshly-logged book flows straight into
    # the by-month Timeline (the client normally sends it, defaulted to this month,
    # but an API caller may omit it). read_seq is auto-assigned in db_write.
    read_month = req.read_month if req.read_month is not None else datetime.date.today().month
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.add_book(
                req.title, req.genre, req.author, req.scores,
                series=req.series or None,
                series_number=req.series_number or None,
                words=req.words or None,
                year_read=req.year_read,
                read_month=read_month,
                user_id=user_id,
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
        con = db_backend.connect(db_write.DB)
        current_queue = [t for (t,) in con.execute(
            "SELECT title FROM read_queue WHERE user_id=? ORDER BY position",
            (user_id,))]
        con.close()
        title_lower = req.title.strip().lower()
        new_queue = [t for t in current_queue
                     if t.strip().lower() != title_lower]
        if len(new_queue) < len(current_queue):
            db_write.update_queue(new_queue, user_id=user_id)
    except Exception:
        pass  # dequeue failure is non-fatal; book was still added

    # Mark the matching TBR recommendation done. A finished book that stays done=0
    # in recommendations is a data-lint ERROR (see scripts/lint_data.py), which
    # blocks the publish — so a finish must always flip its prediction row. The
    # case-insensitive lookup mirrors _maybe_log_delta so it finds the same row;
    # set_done is then called with that row's exact title. Non-fatal: a failure
    # here never rolls back the successful add.
    try:
        con = db_backend.connect(db_write.DB)
        rec = con.execute(
            "SELECT title FROM recommendations "
            "WHERE LOWER(title)=LOWER(?) AND done=0 AND user_id=? ORDER BY id DESC LIMIT 1",
            (req.title, user_id)).fetchone()
        con.close()
        if rec:
            with contextlib.redirect_stdout(io.StringIO()):
                db_write.set_done(rec[0], True, user_id=user_id)
    except Exception:
        pass  # marking done is best-effort; the book was still added

    _invalidate_engine(user_id)

    # If this title had a stored prediction, record the delta automatically.
    # Non-fatal: a failure here never rolls back the successful add_book.
    try:
        _maybe_log_delta(req.title, req.scores, user_id)
    except Exception:
        pass

    # Auto re-predict the unread books whose baseline this book just moved (same
    # author always; same genre only if the genre-tier baseline shifted past the
    # gate). Runs in the BACKGROUND (after this response is sent) so the add
    # returns instantly even when a thin genre or an uncached trigger makes the
    # pass slow; the client polls GET /api/repredict/recent?token=... for the
    # report. Fires AFTER the commit + _invalidate_engine() above, so the engine
    # and correction pool already reflect n=1.
    repredict = None
    if _repred is not None and _rp is not None:
        token = uuid.uuid4().hex
        background_tasks.add_task(
            _run_repredict, token, req.title, req.author, req.genre, req.scores, user_id)
        repredict = {"status": "running", "token": token, "trigger": req.title}

    return {"ok": True, "message": out.replace("✓", "").strip(), "repredict": repredict}


def _run_repredict(token: str, title: str, author: str, genre: str, scores: dict,
                   user_id: str) -> None:
    """Background worker: run the scoped baseline re-prediction and stash the
    report under `token` for the client to poll. Serialized against other adds so
    the SQLite writer never contends. Always records a terminal report (even on
    failure) so the poller never hangs. Scoped to the adding tenant: the engine is
    built for user_id and only that tenant's recommendations are re-predicted."""
    report = None
    try:
        with _repred_lock:
            report = _repred.on_book_added(
                title, author, genre, scores,
                get_engine=lambda: _get_engine(user_id), user_id=user_id)
    except Exception as exc:
        report = None
        print(f"  (background repredict failed for '{title}': {exc})")
    if report is None:
        report = {"trigger": {"title": title, "author": author, "genre": genre},
                  "affected": [], "suppressed_genre_peers": [],
                  "capped_genre_peers": [], "cohort_mean_d_wa": None,
                  "note": "no changes"}
    _repred.record_report(token, report)


@app.get("/api/repredict/recent")
def repredict_recent(token: str, user_id: str = Depends(auth.get_current_user_id)):
    """Poll for a background cohort re-prediction's report by its token. Returns
    {status:"pending"} until the background pass finishes, then {status:"done",
    report:{...}}. Never 404s — a token that never existed just stays pending
    (the client stops polling on its own timeout)."""
    if _repred is None:
        return {"status": "done", "report": None}
    report = _repred.get_report(token)
    if report is None:
        return {"status": "pending"}
    return {"status": "done", "report": report}


def _maybe_log_delta(title: str, act_scores: dict, user_id: str) -> None:
    """Check recommendations for a stored prediction and log delta if found.
    Tenant-scoped: only the caller's own prediction row is matched and logged."""
    con = db_backend.connect(db_write.DB)
    row = con.execute(
        "SELECT genre, author, words, "
        + ", ".join(f'"{c}"' for c in db_write.FICTION_COMPONENTS)
        + ' FROM recommendations WHERE LOWER(title)=LOWER(?) AND user_id=?'
        + ' ORDER BY id DESC LIMIT 1',
        (title, user_id)
    ).fetchone()
    con.close()
    if row is None:
        return  # no prediction on record

    genre, author, words = row[0], row[1], row[2]
    pred_scores = dict(zip(db_write.FICTION_COMPONENTS, row[3:]))
    if not any(v is not None for v in pred_scores.values()):
        return  # recommendation exists but has no component scores

    # Compute pred_wa by running the same WA formula as db_loader
    engine = _get_engine(user_id)
    books, gw, gcw, resid_sd = engine[0], engine[1], engine[2], engine[5]
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

    # Reconstruct the prediction-mechanism metadata (genre/author/words, analog
    # counts = blend weights, correction split, CI, confidence) from the SAME
    # persisted inputs and reference functions the prediction used — read-only,
    # no engine math reimplemented. Best-effort: partial or None on any failure,
    # and log_delta writes whatever survives (missing fields stay NULL).
    meta = None
    if _rp is not None:
        try:
            cache = _rp.load_cache()
            try:
                corr_models = _rp.build_corr_models(books, cache)
            except Exception:
                corr_models = None
            meta = _rp.build_prediction_meta(
                title, author, genre, words, pred_wa, resid_sd,
                books, gw, gcw, cache, corr_models=corr_models)
        except Exception:
            meta = None

    # Tag the delta with the current research model (Opus pipeline) so
    # Opus-era predicted-vs-actual pairs accrue under their own label for a
    # later clean recalibration. Pre-Opus rows stay NULL (not relabeled).
    db_write.log_delta(title, pred_scores, pred_wa, act_scores, act_wa,
                       pred_model=db_write.RESEARCH_MODEL, meta=meta, user_id=user_id)


class EditRatingRequest(BaseModel):
    scores: dict[str, float]


@app.post("/api/books/{title}/scores")
def edit_rating(title: str, req: EditRatingRequest,
                user_id: str = Depends(auth.get_current_user_id)):
    """Update component scores for an existing book via db_write.change_rating."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.change_rating(title, req.scores, user_id=user_id)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not update rating.")
    _invalidate_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


class BookMetadataRequest(BaseModel):
    # Every field optional: this is a PARTIAL update. A field left None is
    # omitted from the write (blank = leave-as-is), matching the edit surface's
    # omit-unchanged policy. `title` carries a rename (cascaded in db_write).
    title: Optional[str] = None
    author: Optional[str] = None
    genre: Optional[str] = None
    series: Optional[str] = None
    series_number: Optional[float] = None
    words: Optional[int] = None
    year_read: Optional[int] = None


def _update_metadata(current_title: str, table: str,
                     req: "BookMetadataRequest", user_id: str) -> dict:
    """Shared handler for the fiction + nonfiction metadata endpoints. Only the
    fields the client actually sent (non-None) are passed through, so an omitted
    field is left unchanged. Returns the db_write report dict. Tenant-scoped."""
    fields = req.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422,
                            detail="No metadata fields provided to update.")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            report = db_write.update_book_metadata(current_title, table, fields,
                                                   user_id=user_id)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not report.get("ok"):
        raise HTTPException(status_code=422,
                            detail=report.get("error") or "Could not update metadata.")
    return report


@app.post("/api/books/{title}/metadata")
def edit_book_metadata(title: str, req: BookMetadataRequest,
                       user_id: str = Depends(auth.get_current_user_id)):
    """Edit a fiction book's metadata (author/genre/series/series_number/words/
    year_read/title) via db_write.update_book_metadata. A genre change re-weights
    WA on the next read; a title change cascades the rename across all tables
    that reference the book by title."""
    report = _update_metadata(title, "books", req, user_id)
    _invalidate_engine(user_id)
    return {"ok": True, "renamed_to": report["renamed_to"],
            "cascade": report["cascade"],
            "message": f"Updated metadata for “{report['renamed_to'] or title}”."}


@app.post("/api/recommendations/{title}/metadata")
def edit_recommendation_metadata(title: str, req: BookMetadataRequest,
                                 user_id: str = Depends(auth.get_current_user_id)):
    """Edit a predicted (TBR) book's metadata — author/genre/series/series_number/
    words — via db_write.update_book_metadata on the recommendations table. Title
    and year_read are not editable there (the write layer rejects them). No engine
    invalidation: recommendations aren't part of the rated-books engine; the
    predicted WA simply re-weights on the next read if the genre changed."""
    _update_metadata(title, "recommendations", req, user_id)
    return {"ok": True, "message": f"Updated metadata for “{title}”."}


class LookupRequest(BaseModel):
    title: str
    author_hint: Optional[str] = None


def _lookup_from_prediction(title: str, user_id: str) -> Optional[dict]:
    """If this title has already been predicted (it exists in the caller's
    recommendations), return its stored metadata in the /api/lookup shape so the
    lookup can skip the LLM entirely — no API key, no spend. Every field the
    lookup surfaces (author/genre/words/series/series_number/blurb) is persisted
    on the recommendation at save time, so nothing is re-derived. Tenant-scoped,
    with the same case-insensitive/trimmed title match _maybe_log_delta uses, so
    the canonical stored title flows back and the eventual add re-finds the
    prediction. Returns None when no prediction is on record."""
    con = db_backend.connect(db_write.DB)
    try:
        row = con.execute(
            "SELECT title, author, genre, words, series, series_number, blurb "
            "FROM recommendations "
            "WHERE LOWER(TRIM(title))=LOWER(TRIM(?)) AND user_id=? "
            "ORDER BY id DESC LIMIT 1",
            (title, user_id),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    stored_title, author, genre, words, series, series_number, blurb = row
    return {
        "title": stored_title or title,
        "author": author or "",
        "genre": genre,
        "words": words,
        "series": series or "",
        "series_number": series_number,
        "blurb": blurb or "",
        "source": "prediction",
    }


@app.post("/api/lookup")
def lookup_book(req: LookupRequest,
                user_id: str = Depends(auth.get_current_user_id)):
    """
    Title-only metadata lookup. If the title has already been predicted (it is in
    the caller's recommendations), the stored prediction metadata is returned
    directly — no LLM call and no API key needed. Otherwise the LLM finds author,
    genre, estimated word count, series, and a blurb, with genre constrained to
    the genre_weights list (global table). Returns the raw lookup result (tagged
    with its `source`) for the user to confirm before filling. Auth-gated.
    """
    title = req.title.strip()

    # Already-predicted books: serve the stored metadata, skip the LLM entirely.
    from_pred = _lookup_from_prediction(title, user_id)
    if from_pred is not None:
        return from_pred

    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")

    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not initialise LLM client: {e}")

    con = db_backend.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()

    hint_author = req.author_hint.strip() if req.author_hint else "unknown"

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
            "source": "llm",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Look-up failed: {e}")


@app.get("/api/tiers")
def get_tiers(year: Optional[int] = None,
              user_id: str = Depends(auth.get_current_user_id)):
    """Return books with tier assignments (S+/S/A/B/C/D/F), optionally filtered by year_read."""
    books = _get_engine(user_id)[0]
    category_components = books.attrs["category_components"]
    snum_map = _series_number_map("books", user_id)

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
def delete_book(title: str, user_id: str = Depends(auth.get_current_user_id)):
    """Permanently delete a rated book via db_write.delete_book (backup-protected)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.delete_book(title, user_id=user_id)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not delete book.")
    _invalidate_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.delete("/api/recommendations/{title}")
def delete_recommendation(title: str,
                          user_id: str = Depends(auth.get_current_user_id)):
    """Permanently delete a TBR recommendation via db_write.delete_recommendation."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.delete_recommendation(title, user_id=user_id)
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


class SignupRequest(BaseModel):
    email: str
    password: str
    invite_code: str


@app.post("/api/signup")
def signup(req: SignupRequest):
    """Invite-code-gated account creation (hosted multi-user). PUBLIC/global —
    the caller isn't authenticated yet, so no Depends(get_current_user_id); the
    invite code is the gate and it (plus the service-role key) lives only on the
    server (see signup.py). 404 when sign-up isn't configured (local/static)."""
    if not signup_mod.SIGNUP_ENABLED:
        raise HTTPException(status_code=404, detail="Sign-up is not enabled here.")
    if not signup_mod.check_invite_code(req.invite_code):
        raise HTTPException(status_code=403, detail="Invalid invite code.")
    email = (req.email or "").strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    try:
        signup_mod.create_user(email, req.password)
    except signup_mod.SignupError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# READ QUEUE — mood-filtered recommendations
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/queue")
def get_queue(user_id: str = Depends(auth.get_current_user_id)):
    """Return the ordered read-queue titles."""
    con = db_backend.connect(db_write.DB)
    titles = [r[0] for r in con.execute(
        "SELECT title FROM read_queue WHERE user_id=? ORDER BY position",
        (user_id,))]
    con.close()
    return {"titles": titles}


class UpdateQueueRequest(BaseModel):
    titles: list[str]


@app.post("/api/queue")
def update_queue(req: UpdateQueueRequest,
                 user_id: str = Depends(auth.get_current_user_id)):
    """Replace the read queue with the given ordered list of titles."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.update_queue(req.titles, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "message": buf.getvalue().strip().replace("✓", "").strip()}


class AddSeriesRequest(BaseModel):
    series_name: str


@app.post("/api/queue/add-series")
def add_series_to_queue(req: AddSeriesRequest,
                        user_id: str = Depends(auth.get_current_user_id)):
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

    con = db_backend.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))

    # Fetch existing data for de-dupe checks (scoped to this tenant)
    read_titles = {t.strip().lower() for (t,) in con.execute(
        "SELECT title FROM books WHERE user_id=?", (user_id,))}
    tbr_titles = {t.strip().lower() for (t,) in con.execute(
        "SELECT title FROM recommendations WHERE done=0 AND user_id=?", (user_id,))}
    current_queue = [t for (t,) in con.execute(
        "SELECT title FROM read_queue WHERE user_id=? ORDER BY position", (user_id,))]
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
                    user_id=user_id,
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
                db_write.update_queue(new_queue, user_id=user_id)
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
def get_read_queue(user_id: str = Depends(auth.get_current_user_id),
                   user_md: dict = Depends(auth.get_current_user_metadata)):
    """Return all not-done recommendations with flat component scores and predicted rank."""
    books, gw, gcw = _get_engine(user_id)[:3]
    rated_wa = books["WA"].values
    # Same-author analog counts drive the conformal interval bucket (author is the
    # engine's innermost density tier). Precompute once so the per-rec lookup is O(1).
    author_counts = books["Author"].value_counts()
    cold_term = _get_cold_term(user_id, user_md.get("word_count_pref"),
                               user_md.get("fav_authors"))

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = db_backend.connect(db_write.DB)
    rows = con.execute(
        f'SELECT title, author, genre, series, series_number, words, blurb, keywords, {comp_cols} '
        f'FROM recommendations WHERE done=0 AND user_id=?',
        (user_id,)
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

        # Cold-start term on the no-analog slice — keeps this rec's WA (and its rank
        # here) consistent with what the Predict page showed for the same book.
        n_author = int(author_counts.get((author or "").strip(), 0))
        wa = _cold_adjust_rec_wa(wa, words, series_number, author, n_author, cold_term)
        predicted_rank = int((rated_wa > wa).sum() + 1)

        rec = {
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
        }
        # Honest 80% prediction interval — the SAME density-bucketed LOO residual
        # table served on the Predict page, keyed by how many same-author books the
        # library holds. The point estimate is a shrunk expected value; this is the
        # calibrated spread around it (bounded to the 0–10 WA scale). Omitted when
        # no residual table is loaded, so a width is never invented.
        iv = _intervals.interval_for(_RESIDUALS, n_author, _ENGINE_HASH)
        if iv is not None:
            hw = iv["half_width"]
            rec["wa_low"] = round(max(0.0, wa - hw), 4)
            rec["wa_high"] = round(min(10.0, wa + hw), 4)
            rec["interval_label"] = iv["bucket_label"]
            rec["interval_stale"] = iv["stale"]
            # "Upside" for ranking: a REALISTIC good outcome, not the interval
            # ceiling. wa_high is the ~P90 outcome (beaten ~1 in 10) — too
            # optimistic to expect across a whole TBR. UPSIDE_FRAC scales the
            # headroom to the ~P75 outcome (beaten ~1 in 4): on the researched LOO
            # residuals the one-sided P75 upside is 43% of the P80 half-width. Still
            # density-scaled, so thin-author/frontier books keep proportionally more
            # upside — just not the best case.
            rec["upside"] = round(min(10.0, wa + UPSIDE_FRAC * hw), 4)
        result.append(rec)

    genres = sorted(set(r["genre"] for r in result if r["genre"]))
    return {"recommendations": result, "genres": genres}


# ─────────────────────────────────────────────────────────────────────────────
# PREDICT — instant analog estimate (free) and grounded research (LLM)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/predict/instant")
def predict_instant(title: str, author: str, genre: str,
                    user_id: str = Depends(auth.get_current_user_id)):
    """Free instant analog prediction — no API call, uses rated-book analogs."""
    try:
        data = _get_engine(user_id)
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
    resp = {
        "title": title, "author": author, "genre": genre,
        "wa_final": round(p["wa_final"], 4),
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
    # Additive 80% conformal interval. Bucket the NEW prediction by how many
    # same-author analogs the library holds — the SAME density definition the
    # LOO residual table uses (intervals.density_bucket), so no miscoverage from
    # drift. Omit the fields entirely when no table is loaded / no width exists.
    if _RESIDUALS is not None:
        n_author = int((books_e["Author"] == author).sum())
        iv = _intervals.interval_for(_RESIDUALS, n_author, _ENGINE_HASH)
        if iv is not None:
            hw = iv["half_width"]
            resp.update({
                "wa_low": round(p["wa_final"] - hw, 4),
                "wa_high": round(p["wa_final"] + hw, 4),
                "bucket": iv["bucket"],
                "bucket_label": iv["bucket_label"],
                "pooled": iv["pooled"],
                "calibrated_at": iv["calibrated_at"],
                "stale": iv["stale"],
            })
    return resp


class ResearchRequest(BaseModel):
    title: str
    author: str
    genre: Optional[str] = None   # None → auto-detect from the LLM
    grounded: bool = False        # False → fast memory scores; True → hybrid
                                  # (web-grounded) upgrade. Default is fast so the
                                  # candidate list scores instantly; the client
                                  # re-requests grounded=True to refine per book.
    force: bool = False           # True → skip every research-cache layer and
                                  # re-research this one book, overwriting its
                                  # cached entry (explicit refresh, never a purge).


@app.post("/api/predict/research")
def predict_research(req: ResearchRequest,
                     user_id: str = Depends(auth.get_current_user_id),
                     user_md: dict = Depends(auth.get_current_user_metadata)):
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
        data = _get_engine(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine build failed: {e}")
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data

    con = db_backend.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()

    cache = _rp.load_cache()
    try:
        scores, conf, blurb, keywords, det_genre, words, from_cache = _rp.research_book(
            req.title, req.author, req.genre, client, cache,
            allowed_genres=allowed_genres, force=req.force,
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
        corr_pool = _correction_pool(user_id, books_e)   # borrow the seed's calibration if new
        pairs, corr_models = _corr_statics(user_id, corr_pool)   # per-run statics, cached
        res = _rp.correct_and_predict(
            req.title, req.author, eff_genre, scores, conf, resid_sd,
            corr_pool, gw_e, gcw_e, cache, blurb=blurb, keywords=keywords,
            corr_models=corr_models, words=words, pairs=pairs,
            cold_term=_get_cold_term(user_id, user_md.get("word_count_pref"),
                                     user_md.get("fav_authors")),
            # Rank / total / grounding counts scope to the tenant's OWN library
            # (books_e), never the seed-borrowed correction pool (corr_pool). The
            # correction VALUE still borrows the seed; only the display denominator
            # changes — so a cold-start reader no longer sees "rank #2 of <seed>".
            rank_pool=books_e,
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
    resp = {
        "title": res["title"], "author": res["author"], "genre": res["genre"],
        "wa": round(res["wa"], 4),
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
    # Additive 80% conformal interval — the SAME density-bucketed table served by
    # /api/predict/instant. n_author is recomputed from the library exactly as the
    # instant path does, so bucketing can't drift from the LOO definition. The band
    # is calibrated on the analog engine's LOO residuals and centred here on the
    # research WA as an empirical error band at this data density (mildly
    # conservative for the usually-tighter research prediction). Omitted entirely
    # when no residual table is loaded — a width is never invented.
    if _RESIDUALS is not None:
        n_author = int((books_e["Author"] == res["author"]).sum())
        iv = _intervals.interval_for(_RESIDUALS, n_author, _ENGINE_HASH)
        if iv is not None:
            hw = iv["half_width"]
            resp.update({
                "wa_low": round(res["wa"] - hw, 4),
                "wa_high": round(res["wa"] + hw, 4),
                "bucket": iv["bucket"],
                "bucket_label": iv["bucket_label"],
                "pooled": iv["pooled"],
                "calibrated_at": iv["calibrated_at"],
                "stale": iv["stale"],
            })
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER — generate candidates then score them individually
# ─────────────────────────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    request: str
    # Optional upper bound. When omitted, the LLM infers the count from the
    # request wording (e.g. "the 5 main books of X" → 5).
    max_candidates: Optional[int] = None


@app.post("/api/discover/candidates")
def discover_candidates(req: DiscoverRequest,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Generate candidate book titles for a free-text request (1 API call)."""
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")

    books = _get_engine(user_id)[0]
    cache = _rp.load_cache()

    con = db_backend.connect(db_write.DB)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    tbr_books = [(t or "", a or "") for t, a in con.execute(
        "SELECT title, author FROM recommendations WHERE user_id=?", (user_id,))]
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
def generate_recommendation_meta(req: GenerateMetaRequest,
                                 user_id: str = Depends(auth.get_current_user_id)):
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
            db_write.set_recommendation_meta(req.title, blurb or None, keywords or None,
                                             user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "blurb": blurb or "", "keywords": keywords or ""}


# ─────────────────────────────────────────────────────────────────────────────
# READING STATS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/reading/stats")
def get_reading_stats(user_id: str = Depends(auth.get_current_user_id)):
    """Reading stats: totals, per-year, by-genre, by-author breakdowns."""
    books = _get_engine(user_id)[0]
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
def get_reading_status(user_id: str = Depends(auth.get_current_user_id),
                       user_md: dict = Depends(auth.get_current_user_metadata)):
    """Queue-derived reading status: last read, currently reading, reading next."""
    books, gw, gcw = _get_engine(user_id)[:3]
    rated_wa = books["WA"].values
    total_rated = len(books)
    author_counts = books["Author"].value_counts()
    cold_term = _get_cold_term(user_id, user_md.get("word_count_pref"),
                               user_md.get("fav_authors"))

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)

    con = db_backend.connect(db_write.DB)

    # Queue positions 1 and 2
    queue_titles = [r[0].strip() for r in con.execute(
        "SELECT title FROM read_queue WHERE user_id=? ORDER BY position LIMIT 2",
        (user_id,)).fetchall()]

    def _slot_from_rec(title: str):
        """Build a status slot from the recommendations table (this tenant only)."""
        row = con.execute(
            f'SELECT author, genre, series, series_number, words, {comp_cols} '
            f'FROM recommendations WHERE LOWER(TRIM(title))=LOWER(TRIM(?)) AND user_id=?',
            (title, user_id)
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
        n_author = int(author_counts.get((author or "").strip(), 0))
        wa = _cold_adjust_rec_wa(wa, words, series_number, author, n_author, cold_term)
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
        "SELECT title FROM books WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,)
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
                "series_number": _series_number_map("books", user_id).get(lr_title.lower()),
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
def set_year_read(req: SetYearRequest,
                  user_id: str = Depends(auth.get_current_user_id)):
    """Set year_read on a rated book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_year_read(req.title, req.year, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not set year.")
    _invalidate_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


# ─────────────────────────────────────────────────────────────────────────────
# SERIES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/series")
def get_series(user_id: str = Depends(auth.get_current_user_id)):
    """Series rankings: per-series aggregates sorted by Adjusted WA."""
    books = _get_engine(user_id)[0]
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
def get_series_tiers(user_id: str = Depends(auth.get_current_user_id)):
    """Series tier list: same bands as book tier list but by Adjusted WA (S+ >= 9.0)."""
    books = _get_engine(user_id)[0]
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
def get_timeline(user_id: str = Depends(auth.get_current_user_id)):
    """Reading timeline: per-year AND per-month book count, avg WA, five category
    averages, avg words. The per-month breakdown covers books with a read_month;
    year-only books still appear in the per-year rows."""
    books = _get_engine(user_id)[0]
    if len(books) == 0:  # brand-new tenant: views_mod.timeline indexes 'Year'
        return {"rows": [], "months": [], "categories": views_mod.CATEGORY_ORDER}
    tl = views_mod.timeline(books)
    if tl.empty:
        return {"rows": [], "months": [], "categories": views_mod.CATEGORY_ORDER}
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
    months = timeline_month.by_month(
        books,
        _read_month_map(user_id, "books"),
        views_mod.category_average,
        views_mod.total_average,
        views_mod._category_components(books),
        views_mod.CATEGORY_ORDER,
    )
    return {"rows": rows, "months": months, "categories": views_mod.CATEGORY_ORDER}


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
def get_nf_books(user_id: str = Depends(auth.get_current_user_id)):
    """All nonfiction books, ranked by Total Average (the workbook's nonfiction
    ranking). Carries both `total_average` and the Quality-lean `wa`."""
    books, gw, gcw = _get_nf_engine(user_id)
    bt = nfe.add_total_average(books)
    cat_components = books.attrs["category_components"]
    snum_map = _series_number_map("nonfiction_books", user_id)
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
def get_nf_tiers(user_id: str = Depends(auth.get_current_user_id)):
    """Nonfiction tier list, banded by Total Average (reuses the fiction
    thresholds: S+ >= 9.5, then 9/15/25/25/15/10% percentiles)."""
    books, gw, gcw = _get_nf_engine(user_id)
    bt = nfe.add_total_average(books)
    cat_components = books.attrs["category_components"]
    snum_map = _series_number_map("nonfiction_books", user_id)
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
def get_nf_series(user_id: str = Depends(auth.get_current_user_id)):
    """Nonfiction series rollup (ranked by Avg Total Average). Normally empty —
    nonfiction has no series yet."""
    books = _get_nf_engine(user_id)[0]
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
def get_nf_series_tiers(user_id: str = Depends(auth.get_current_user_id)):
    """Nonfiction series tier list. Normally empty (no nonfiction series yet)."""
    books = _get_nf_engine(user_id)[0]
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
def get_nf_timeline(user_id: str = Depends(auth.get_current_user_id)):
    """Per-year AND per-month nonfiction timeline (Quality/Aesthetics/Theme). The
    per-month breakdown covers nonfiction books with a read_month."""
    books = _get_nf_engine(user_id)[0]
    cats = list(NF_CAT_ORDER)
    if len(books) == 0:  # brand-new tenant: nfe.timeline indexes 'Year'
        return {"rows": [], "months": [], "categories": cats}
    tl = nfe.timeline(books)
    if tl.empty:
        return {"rows": [], "months": [], "categories": cats}
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
    months = timeline_month.by_month(
        books,
        _read_month_map(user_id, "nonfiction_books"),
        nfe.category_average,
        nfe.total_average,
        nfe._category_components(books),
        cats,
    )
    return {"rows": rows, "months": months, "categories": cats}


@app.get("/api/nonfiction/reading/stats")
def get_nf_reading_stats(user_id: str = Depends(auth.get_current_user_id)):
    """Nonfiction reading stats. by_genre is omitted (no nonfiction genre
    taxonomy yet); by_author carries the breakdown."""
    books = _get_nf_engine(user_id)[0]
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
def get_nf_reading_status(user_id: str = Depends(auth.get_current_user_id)):
    """Nonfiction reading status. currently-reading / reading-next come from the
    nonfiction_books.status column (there is no nonfiction queue); last_read is
    the most recently added nonfiction book."""
    books = _get_nf_engine(user_id)[0]
    bt = nfe.add_total_average(books)
    total = int(len(bt))
    ta_vals = bt["Total Average"].values
    snum = _series_number_map("nonfiction_books", user_id)

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

    con = db_backend.connect(db_write.DB)
    try:
        cur = con.execute("SELECT title FROM nonfiction_books "
                          "WHERE status='currently-reading' AND user_id=? LIMIT 1",
                          (user_id,)).fetchone()
        nxt = con.execute("SELECT title FROM nonfiction_books "
                          "WHERE status='reading-next' AND user_id=? LIMIT 1",
                          (user_id,)).fetchone()
        last = con.execute("SELECT title FROM nonfiction_books "
                           "WHERE user_id=? ORDER BY id DESC LIMIT 1",
                           (user_id,)).fetchone()
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
    read_month: Optional[int] = None  # 1-12; defaults to the current month


@app.post("/api/nonfiction/books")
def add_nf_book(req: NonfictionAddRequest,
                user_id: str = Depends(auth.get_current_user_id)):
    """Add a rated nonfiction book via db_write.add_nonfiction_book."""
    read_month = req.read_month if req.read_month is not None else datetime.date.today().month
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_nonfiction_book(
                title=req.title, author=req.author, genre=req.genre,
                scores=req.scores, series=req.series,
                series_number=req.series_number, words=req.words,
                year_read=req.year_read, read_month=read_month, user_id=user_id,
            )
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok or "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not add book.")
    _invalidate_nf_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NonfictionScoresRequest(BaseModel):
    scores: dict


@app.post("/api/nonfiction/books/{title}/scores")
def edit_nf_scores(title: str, req: NonfictionScoresRequest,
                   user_id: str = Depends(auth.get_current_user_id)):
    """Update component scores on a nonfiction book (recomputes its averages)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.change_nonfiction_rating(title, req.scores, user_id=user_id)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not update scores.")
    _invalidate_nf_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.post("/api/nonfiction/books/{title}/metadata")
def edit_nf_book_metadata(title: str, req: BookMetadataRequest,
                          user_id: str = Depends(auth.get_current_user_id)):
    """Edit a nonfiction book's metadata via db_write.update_book_metadata (same
    partial-update + rename-cascade behaviour as the fiction endpoint, over the
    nonfiction tables)."""
    report = _update_metadata(title, "nonfiction_books", req, user_id)
    _invalidate_nf_engine(user_id)
    return {"ok": True, "renamed_to": report["renamed_to"],
            "cascade": report["cascade"],
            "message": f"Updated metadata for “{report['renamed_to'] or title}”."}


@app.get("/api/nonfiction/valid-genres")
def get_nf_valid_genres(user_id: str = Depends(auth.get_current_user_id)):
    """Nonfiction genres valid for the metadata dropdown: the global set PLUS the
    caller's own private genres."""
    con = db_backend.connect(db_write.DB)
    genres = {r[0] for r in con.execute("SELECT genre FROM nonfiction_genre_weights")}
    genres |= {r[0] for r in con.execute(
        "SELECT DISTINCT genre FROM nonfiction_genre_weight_overrides WHERE user_id=?",
        (user_id,))}
    con.close()
    return sorted(genres)


@app.delete("/api/nonfiction/books/{title}")
def delete_nf_book(title: str,
                   user_id: str = Depends(auth.get_current_user_id)):
    """Permanently delete a nonfiction book via db_write.delete_nonfiction_book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.delete_nonfiction_book(title, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok or "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not delete book.")
    _invalidate_nf_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.post("/api/nonfiction/reading/set-year")
def set_nf_year(req: SetYearRequest,
                user_id: str = Depends(auth.get_current_user_id)):
    """Set year_read on a nonfiction book."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_nonfiction_year_read(req.title, req.year, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not set year.")
    _invalidate_nf_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NonfictionResearchRequest(BaseModel):
    title: str
    author: str
    genre: Optional[str] = None


@app.post("/api/nonfiction/predict/research")
def predict_nf_research(req: NonfictionResearchRequest,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Grounded nonfiction prediction: one LLM call scores the 8 components, then
    they roll up through the SAME nonfiction math (category averages, Quality-lean
    WA, Total Average) and are ranked by Total Average against the rated nonfiction
    books. Always low-confidence at n=6. No TBR save (there is no nonfiction
    recommendations table)."""
    if _nr is None:
        raise HTTPException(status_code=500, detail="nonfiction_research not available")
    try:
        data = _get_nf_engine(user_id)
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
def discover_nf_candidates(req: NonfictionDiscoverRequest,
                           user_id: str = Depends(auth.get_current_user_id)):
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
    con = db_backend.connect(db_write.DB)
    have = {r[0].strip().lower() for r in con.execute(
        "SELECT title FROM nonfiction_books WHERE user_id=?", (user_id,)) if r[0]}
    have |= {r[0].strip().lower() for r in con.execute(
        "SELECT title FROM nonfiction_recommendations WHERE user_id=?", (user_id,)) if r[0]}
    con.close()
    fresh = [c for c in cands if c["title"].strip().lower() not in have]
    note = "" if fresh else "Every suggestion is already in your library or TBR — try a different request."
    return {"candidates": fresh, "request": request, "note": note}


# ─── Nonfiction TBR (recommendations + read queue) ───────────────────────────

@app.get("/api/nonfiction/read-queue")
def get_nf_read_queue(user_id: str = Depends(auth.get_current_user_id)):
    """Not-done nonfiction recommendations with components, category averages,
    Total Average / WA (computed on read), and predicted rank by Total Average."""
    books, gw, gcw = _get_nf_engine(user_id)
    bt = nfe.add_total_average(books)
    rated_ta = bt["Total Average"].values

    COMPONENTS = db_write.NONFICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = db_backend.connect(db_write.DB)
    rows = con.execute(
        f'SELECT title, author, genre, series, series_number, words, blurb, keywords, {comp_cols} '
        f'FROM nonfiction_recommendations WHERE done=0 AND user_id=?',
        (user_id,)
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
def add_nf_recommendation(req: NonfictionRecRequest,
                          user_id: str = Depends(auth.get_current_user_id)):
    """Save a researched nonfiction book to the TBR."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_nonfiction_recommendation(
                title=req.title, author=req.author, genre=req.genre,
                scores=req.scores, series=req.series,
                series_number=req.series_number, words=req.words,
                blurb=req.blurb, keywords=req.keywords, user_id=user_id)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok or "✗" in out:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not save.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.delete("/api/nonfiction/recommendations/{title}")
def delete_nf_recommendation(title: str,
                             user_id: str = Depends(auth.get_current_user_id)):
    """Remove a nonfiction TBR recommendation."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.delete_nonfiction_recommendation(title, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not delete.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NfDoneRequest(BaseModel):
    done: bool = True


@app.post("/api/nonfiction/recommendations/{title}/done")
def set_nf_done(title: str, req: NfDoneRequest,
                user_id: str = Depends(auth.get_current_user_id)):
    """Mark a nonfiction recommendation done / not-done."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_nonfiction_done(title, req.done, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not update.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.get("/api/nonfiction/queue")
def get_nf_queue(user_id: str = Depends(auth.get_current_user_id)):
    """Ordered nonfiction read-queue titles."""
    con = db_backend.connect(db_write.DB)
    titles = [r[0] for r in con.execute(
        "SELECT title FROM nonfiction_read_queue WHERE user_id=? ORDER BY position",
        (user_id,))]
    con.close()
    return {"titles": titles}


@app.post("/api/nonfiction/queue")
def update_nf_queue(req: UpdateQueueRequest,
                    user_id: str = Depends(auth.get_current_user_id)):
    """Replace the nonfiction read queue with the given ordered titles."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.update_nonfiction_queue(req.titles, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "message": buf.getvalue().strip().replace("✓", "").strip()}


@app.get("/api/stats")
def get_combined_stats(user_id: str = Depends(auth.get_current_user_id)):
    """Combined Fiction + Nonfiction stats. The two WAs come from different
    formulas, so the cross-type ranking is by TOTAL AVERAGE (the unweighted mean
    of category averages — directly comparable across types on the same 0-10
    scale). WA is shown only inside each type. Tier distributions are reported
    per type (fiction banded by WA, nonfiction by Total Average) since the bases
    differ. Reuses the fiction + nonfiction engines; computes no new math."""
    # Worldbuilding is stored as a literal 0 for books where it doesn't apply
    # (realist genres, plus a few character-driven SF/literary titles). The app
    # treats a 0 component as "not scored" everywhere else — it sorts to the
    # bottom and renders as "—" — but Total Average (the unweighted mean of the
    # category averages) only skips a category when its components are NaN, not 0.
    # So those books had a worldbuilding average of 0 folded into their Total
    # Average, unfairly sinking them in this cross-type ranking. Mask the 0
    # sentinels to NaN on a COPY (never the cached engine frame) so the canonical
    # views.add_total_average skips the empty worldbuilding category exactly as
    # its docstring intends. Only Total Average is affected; WA is precomputed in
    # the loader and untouched (the 0 component values contribute 0 either way).
    _fbooks = _get_engine(user_id)[0]
    _fmasked = _fbooks.copy()
    _fmasked.attrs = dict(_fbooks.attrs)
    for _wbc in _fmasked.attrs.get("category_components", {}).get("Worldbuilding", []):
        if _wbc in _fmasked.columns:
            _fmasked.loc[_fmasked[_wbc] == 0, _wbc] = float("nan")
    fbt = views_mod.add_total_average(_fmasked)
    nbt = nfe.add_total_average(_get_nf_engine(user_id)[0])

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


def _enrich_recommendation(req: "SaveRecommendationRequest", user_id: str):
    """Generate the rich house-style blurb and resolve series + ordinal at SAVE
    time (deferred from scoring so the two extra LLM calls are only paid for
    books actually kept). Best-effort: returns (blurb, series, series_number),
    falling back to whatever the request already carried if the LLM is
    unavailable or the calls fail. The blurb's WA/CI frame is built from the
    caller's OWN engine (user_id-scoped)."""
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
            books_e, gw_e, gcw_e, _coeffs, _r2, _resid_sd, _ginfo, _up = _get_engine(user_id)
            genre = req.genre
            wa = 0.0
            for cat in db_loader.CATEGORY_OF_INTEREST:
                wcat = db_loader._weighted_cat_avg(req.scores, genre, cat, gcw_e)
                wa += wcat * ((gw_e.get(genre, {}) or {}).get(cat, 0) or 0)
            n_genre = int((books_e["Genre"] == genre).sum())
            n_author = int((books_e["Author"] == req.author).sum())
            # Confidence frame for the blurb = the SAME served conformal 80% band
            # (density-bucketed by same-author analogs), never the overconfident
            # ±1.645·resid_sd band. Soft default only if no residual table loaded.
            _iv = _intervals.interval_for(_RESIDUALS, n_author, _ENGINE_HASH)
            half = _iv["half_width"] if _iv else 0.5
            ci = (wa - half, wa + half)
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
def save_recommendation(req: SaveRecommendationRequest,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Save a researched book to recommendations (TBR list). Generates the rich
    blurb and resolves series/ordinal here (deferred from scoring) so those LLM
    calls are only spent on books the reader keeps."""
    blurb, series, series_number = _enrich_recommendation(req, user_id)

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
                user_id=user_id,
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
def get_calibration_health(user_id: str = Depends(auth.get_current_user_id)):
    """
    Free model-health metrics from the cached engine build:
    R², residual SD, regression coefficients, and per-genre bias/trust.
    """
    books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream = _get_engine(user_id)
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
def run_loo_validation(user_id: str = Depends(auth.get_current_user_id)):
    """
    Honest leave-one-out validation. Refits the engine ~n times — SLOW (seconds).
    Triggered explicitly by the user on the Calibration page, not on every load.
    """
    books, gw, gcw = _get_engine(user_id)[:3]
    try:
        result = ve.run_loo(books=books, gw=gw, gcw=gcw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LOO validation failed: {e}")
    return result


@app.get("/api/calibration/researcher-comparison")
def get_researcher_comparison(user_id: str = Depends(auth.get_current_user_id)):
    """Serve the last memory-vs-web-grounded per-component MAE comparison, if one
    has been run. This reads the static output of compare_researchers.py — a
    measurement artifact, NOT a live metric — so it never triggers LLM spend or
    touches the engine. Returns 404 when the comparison hasn't been run yet.
    Auth-gated (diagnostic) though the artifact it serves is not per-tenant."""
    path = os.path.join(PROJECT_ROOT, "compare_researchers_result.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No researcher comparison run yet.")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Could not read comparison: {e}")


@app.get("/api/track-record")
def get_track_record():
    """Public walk-forward track record: predicted-vs-actual accuracy, the
    rolling-MAE "getting smarter" curve, MAE by genre, and served-interval
    coverage — assembled from the committed validation/ artifacts.

    READ-ONLY: reads the committed walk-forward files (never runs the harness,
    no API spend, no books.db dependency) and computes served-interval coverage
    through the canonical `intervals` module. Returns 404 when the artifacts are
    absent (allow_404 in the export → JSON null → the page shows a graceful
    "not yet available" state). See track_record.py for the honest/leaky policy."""
    payload = tr.build_track_record()
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="Walk-forward artifacts not available (run walkforward.py).",
        )
    return payload


@app.get("/api/engine-parameters")
def get_engine_parameters(user_id: str = Depends(auth.get_current_user_id),
                          user_md: dict = Depends(auth.get_current_user_metadata)):
    """Live engine parameters for the "How the Engine Works" page: the
    14-component schema + per-genre weights, the served empirical-Bayes shrinkage
    constants, the conformal-interval config + per-bucket half-widths, the
    research/discover model ids, and the WA-from-categories regression diagnostic.

    TENANT-SCOPED: the schema/weights/library size come from the CALLER'S warm
    engine (their effective weights, overrides included), and the cold-start
    block reflects THEIR term — fitted on their own library once they cross the
    fit threshold, else their onboarding word-count preference. A below-threshold
    tenant is flagged as running on the borrowed seed calibration. Every
    drift-prone constant is read straight off the modules that implement it
    (reresearch_and_measure / research_predict / intervals) — nothing is
    hardcoded here, so the page can never silently disagree with the engine. No
    prediction is run, nothing is written, no tokens are spent. Deterministic for
    the default user (no timestamps/HEAD), so it snapshots byte-identically.
    Validation baselines (walk-forward MAE, measured coverage) live on
    /api/track-record — this page reuses that so the two can't drift apart."""
    try:
        books, gw, gcw, _coeffs, r2, resid_sd, _ginfo, _upstream = _get_engine(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Engine build failed: {e}")
    borrowed = _uid(user_id) != SEED_USER_ID and len(books) < MIN_OWN_FIT
    return ep.build_engine_parameters(
        books, gw, gcw, r2, resid_sd, residuals=_RESIDUALS,
        cold_term=_get_cold_term(user_id, user_md.get("word_count_pref"),
                                 user_md.get("fav_authors")),
        model_source="borrowed_seed" if borrowed else "own",
        min_own_fit=MIN_OWN_FIT,
    )


@app.get("/api/delta-log")
def get_delta_log(user_id: str = Depends(auth.get_current_user_id)):
    """Prediction-vs-actual deltas for genuinely-read books, in reading order
    (most-recently-read first → least-recently-read last, by read_seq).

    Shows one row per book the tenant has actually FINISHED
    (`books.status='finished'`), excluding `repredict_on_add` audit rows (whose
    "actual" is a re-prediction, not a rating) and collapsing duplicate history
    rows to the most authoritative one. See delta_log_view.visible_rows. The
    displayed `pred_*` is the frozen value stored at log time — never recomputed
    here, so it does not move when the engine is retrained or reweighted."""
    COMPS = db_write.FICTION_COMPONENTS

    def _col(c: str) -> str:
        return c.replace(" ", "_").replace("-", "_")

    pred_cols = [f'"pred_{_col(c)}" as "pred_{_col(c)}"' for c in COMPS]
    act_cols  = [f'"act_{_col(c)}"  as "act_{_col(c)}"'  for c in COMPS]
    d_cols    = [f'"d_{_col(c)}"    as "d_{_col(c)}"'    for c in COMPS]
    base_cols = ["id", "title", "logged_at", "pred_wa", "act_wa", "d_wa"]
    # `tag` is fetched only to classify rows (genuine vs re-prediction audit, and
    # backfill vs retro_sweep for dedup); it is stripped before the response.
    sel = ", ".join(base_cols + pred_cols + act_cols + d_cols + ["tag"])
    con = db_backend.connect(db_write.DB)
    rows = con.execute(
        f"SELECT {sel} FROM delta_log WHERE user_id=? ORDER BY id DESC",
        (user_id,)
    ).fetchall()
    # Authoritative "genuinely finished and rated" set for this tenant, plus each
    # book's read_seq (reading-order rank; higher = more recent) so the page can
    # order most-recently-read → least, and its (year_read, read_month) for the
    # "read Mon Year" label. The Delta Log is a historical accuracy record, so
    # eligibility keys off the explicit read state — not merely "an act_* value
    # exists" (repredict/backfill rows carry those too).
    finished = set()
    read_order: dict = {}     # key -> encoded reading rank (sorts the page)
    read_when: dict = {}      # key -> (year_read, read_month) (labels the card)
    for (t, yr, mo, seq) in con.execute(
        "SELECT title, year_read, read_month, read_seq FROM books "
        "WHERE user_id=? AND status=?",
        (user_id, "finished")
    ).fetchall():
        key = (t or "").strip().lower()
        finished.add(key)
        read_when[key] = (yr, mo)
        # Rank = (YYYYMM) · 1e6 + read_seq, encoded into one descending-sortable
        # int: order by (year, month) first — so a back-dated add lands in ITS
        # month, not just at the top — then read_seq breaks same-month ties (and
        # is the add order). Books with a read_seq but no month still sort by year.
        if yr is not None:
            read_order[key] = (int(yr) * 100 + (int(mo) if mo else 0)) * 1_000_000 \
                + (int(seq) if seq else 0)
    con.close()

    col_names = (
        base_cols
        + [f"pred_{_col(c)}" for c in COMPS]
        + [f"act_{_col(c)}"  for c in COMPS]
        + [f"d_{_col(c)}"    for c in COMPS]
        + ["tag"]
    )

    entries = [dict(zip(col_names, r)) for r in rows]
    # Requirement 1 (only genuinely-read books, never a re-prediction audit row)
    # + dedup to one authoritative row per book, ordered oldest-read → newest via
    # read_order. Pure, unit-tested: delta_log_view.
    entries = delta_log_view.visible_rows(
        entries, finished, db_write.DELTA_BACKFILL_MARKER, read_order=read_order)
    for e in entries:
        e.pop("tag", None)   # internal classifier; not part of the response
        # Read date labels the card ("read Mon Year"); logged_at is the forecast
        # capture time, which for backfilled rows is a bulk marker, not the read day.
        yr, mo = read_when.get((e.get("title") or "").strip().lower(), (None, None))
        e["read_year"] = yr
        e["read_month"] = mo

    # Per-component mean delta across the shown (genuine, deduped) entries
    drift: dict = {}
    for c in COMPS:
        vals = [e[f"d_{_col(c)}"] for e in entries if e.get(f"d_{_col(c)}") is not None]
        drift[c] = round(sum(vals) / len(vals), 4) if vals else None

    return {
        "entries": entries,
        "components": COMPS,
        "drift": drift,
    }
