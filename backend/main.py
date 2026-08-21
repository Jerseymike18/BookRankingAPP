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
import logging
import time
from collections import defaultdict, deque, OrderedDict
import db_backend
import uuid
import threading
from contextlib import asynccontextmanager

# Make the project root importable regardless of where uvicorn is launched from
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)  # books.db is resolved relative to cwd

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, AfterValidator
from typing import Optional, Annotated

import pandas as pd
import numpy as np
import db_loader
import db_write
import cache_sync
import goodreads_import
import import_enrich
import star_priors
import user_weights
import score_anchors as sa
import predict_engine as pe
import views as views_mod
import validate_engine as ve
import nonfiction_engine as nfe
import auth
import signup as signup_mod
import track_record as tr
import engine_validation as ev
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
    import nonfiction_walkforward as _nfw  # read-only LOO helper (nonfiction interval)
except ImportError:
    _rp = None  # server starts fine; LLM endpoints return 503
    _rl = None
    _nr = None
    _nfw = None

# repredict_on_add pulls in the LLM research path (rp + hybrid); guarded the same
# way so the server still starts when those deps are absent (feature just no-ops).
try:
    import repredict_on_add as _repred
except Exception:
    _repred = None

# How many uvicorn worker PROCESSES this deployment runs (Procfile:
# --workers ${WEB_CONCURRENCY:-2}). Read here, not to spawn anything, but because
# several in-process budgets have to be divided by it to keep their AGGREGATE the
# same — see _rate_limit and the background-grounding executor below.
WORKERS = max(1, int(os.environ.get("WEB_CONCURRENCY", "1")))

# Serializes background cohort re-predictions so overlapping adds never contend on
# the writer. The work runs off the request thread; the add-book response returns
# first. CrossProcessLock, not threading.Lock: under --workers this has to hold
# across PROCESSES too, which on Postgres it does via a session advisory lock (on
# SQLite it degrades to exactly the threading.Lock it replaced).
_repred_lock = db_write.CrossProcessLock("repredict")

# After-save background grounding (Phase 3 of the latency work): when a book is
# saved to recommendations with memory-only scores, ground it server-side and
# upgrade its stored prediction — off the interactive path. A DEDICATED small
# executor (not FastAPI's request threadpool) drains the work so pending
# groundings queue here instead of holding request threads, and its width bounds
# how many web_search calls run at once. That bound is deliberate and load-bearing:
# the concurrency A/B (2026-07-21) showed grounded calls trip the Anthropic rate
# limiter and self-throttle past ~5-6 concurrent, so background grounding stays
# LOW (default 3) to leave rate-budget headroom for a user's live Discover refine.
# BACKGROUND_GROUND_CONCURRENCY=0 disables the feature (saves stay memory-scored).
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
# Divided by the worker count: this bound is a rate-budget bound against the
# Anthropic API, and the 2026-07-21 A/B found grounded calls self-throttle past
# ~5-6 concurrent. Each worker owns its own executor, so N workers at the full
# width would be N x the concurrency the A/B actually validated.
_BG_GROUND_CONCURRENCY = int(os.environ.get("BACKGROUND_GROUND_CONCURRENCY", "3"))
_BG_GROUND_CONCURRENCY = (max(1, _BG_GROUND_CONCURRENCY // max(1, int(
    os.environ.get("WEB_CONCURRENCY", "1")))) if _BG_GROUND_CONCURRENCY > 0 else 0)
_ground_executor = (_ThreadPoolExecutor(max_workers=_BG_GROUND_CONCURRENCY,
                                        thread_name_prefix="bg-ground")
                    if _BG_GROUND_CONCURRENCY > 0 else None)

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
_TENANT_CACHE_MAX = int(os.environ.get("TENANT_CACHE_MAX", "256"))


class _LRUCache:
    """Thread-safe, size-capped LRU exposing the dict subset the cache sites use:
    c[k], c[k]=v, c.get(k), c.pop(k, default), k in c, len(c). Evicts the least-
    recently-used entry past `maxsize`. Its lock guards ONLY the tiny dict ops,
    never the expensive value construction (callers build values outside the
    cache) — so it adds no contention to engine builds and cannot deadlock on the
    seed-model recursion inside _build_engine_for."""

    def __init__(self, maxsize: int):
        self._d = OrderedDict()
        self._max = max(1, maxsize)
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            if key not in self._d:
                return default
            self._d.move_to_end(key)
            return self._d[key]

    def __getitem__(self, key):
        with self._lock:
            self._d.move_to_end(key)
            return self._d[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)   # evict least-recently-used

    def pop(self, key, default=None):
        with self._lock:
            return self._d.pop(key, default)

    def clear(self):
        with self._lock:
            self._d.clear()

    def __contains__(self, key):
        with self._lock:
            return key in self._d

    def __len__(self):
        with self._lock:
            return len(self._d)


# Capped so total memory can't grow without bound as the tenant count rises (each
# engine tuple holds a books DataFrame + a fitted regression). Eviction is always
# safe — every value is a pure function of committed DB state, so a miss rebuilds
# the identical object. The monotonic _engine_epoch token (defined below) is
# deliberately NOT capped: it must never reset, or _corr_statics staleness
# detection could be fooled.
_engine_cache = _LRUCache(_TENANT_CACHE_MAX)
_nf_engine_cache = _LRUCache(_TENANT_CACHE_MAX)

# Guards the single-flight in _get_engine: which (uid, epoch-key) pairs are being
# built right now, and the condition concurrent callers wait on. The token carries
# BOTH the uid and the epoch key — the uid because _build_engine_for recurses into
# the seed's engine and a new tenant shares the seed's (0, 0) epoch key (see
# _get_engine), and the epoch key so a caller waiting on a build that a fresh write
# has already superseded rebuilds rather than accepting a stale tuple. Only the tiny
# set operations happen under the lock — never the build itself.
_engine_building: set = set()
_engine_build_cv = threading.Condition()


def _uid(user_id):
    return user_id or db_backend.DEFAULT_USER_ID


# Cold-start prior (Phase-4 K_USER_PRIOR, v2 — SMOOTH). A tenant without enough
# books to fit a stable model of their own shrinks toward the seed tenant's fitted
# prediction model (coeffs / genre bias+trust / upstream) instead of switching to
# it wholesale. Their OWN books + weights are still used for listing and WA ranking
# — WA is computed from their EFFECTIVE weights (the global defaults overlaid with
# any of their own overrides) + their own scores in db_loader, so rankings stay
# correctly per-user. This crash-proofs the 0-book case (pe.fit_regression on an
# empty frame raises — the multi-tenant cold-start 500) and gives brand-new users
# working predictions from book #1.
#
# The seed is the local single-user (Michael / DEFAULT_USER_ID), who ALWAYS fits
# his own model with no blending at all (the short-circuit in _build_engine_for),
# so his behavior is byte-identical and the walk-forward gate is intact.
#
# v1 was a hard switch at MIN_OWN_FIT: below it a tenant ran on 100% of someone
# else's calibration, at it they snapped to 100% of their own. v2 replaces that
# cliff with empirical-Bayes shrinkage, which is also what the engine already does
# internally (pe._shrink, genre trust n/(n+8)) — this just extends the same idea to
# the per-tenant model itself. NOTHING here reimplements prediction math: the
# read-only engine's fit functions are called unchanged and only their OUTPUTS are
# combined, exactly as the v1 borrow already did.
SEED_USER_ID = db_backend.DEFAULT_USER_ID
# Retained as the CORRECTION-POOL threshold (_correction_pool) and as the figure
# reported on /api/engine-parameters — no longer a model on/off switch.
MIN_OWN_FIT = 15

# Below this many books no own fit is ATTEMPTED — not a taste judgement but a
# numerical floor: fit_regression solves 5 parameters and takes np.std(resid,
# ddof=5), so a smaller frame is underdetermined and its resid_sd degenerate.
OWN_FIT_FLOOR = 8
# Model-level shrinkage constant. Chosen so the own-fit weight is exactly ½ at the
# legacy MIN_OWN_FIT=15 threshold: the old switch point is now the MIDPOINT of the
# ramp rather than a cliff (15 - OWN_FIT_FLOOR = 7 = K_MODEL -> w = 7/14).
K_MODEL = 7.0
# Per-GENRE shrinkage constant for the genre bias/trust blend. Deliberately equal
# to the constant inside pe.genre_bias_and_trust (trust = n/(n+8)) so the blend and
# the engine's own genre weighting have the same shape.
K_GENRE = 8.0


def _own_fit_weight(n_books: int) -> float:
    """Weight on the tenant's OWN model-level fit; 1 - w rides the seed prior.

    Ramps from 0 at OWN_FIT_FLOOR (continuous — no jump at the floor, because the
    numerator starts at 0 there) toward 1 as the library grows. Governs only the
    near-deterministic pieces (the WA-from-categories regression, R^2 ~ 0.99, and
    the upstream component models); per-GENRE taste shrinks separately on its own
    count in _blend_ginfo, which is where a reader's actual preferences live."""
    m = max(0, int(n_books) - OWN_FIT_FLOOR)
    return m / (m + K_MODEL)


def _blend_ginfo(own: dict, seed: dict) -> dict:
    """Per-genre blend of pe.genre_bias_and_trust output.

    The denominator is each genre's OWN count, not the library-wide one: a tenant
    with 12 books may hold 9 in one genre and 0 in six others, and shrinking those
    identically would both hold back the genre they have real evidence for and
    over-trust the ones they have none for. A genre absent from `own` has n=0 and
    collapses exactly to the seed entry (v1's behaviour), and one absent from the
    seed passes through unshrunk."""
    out = {}
    for g in set(own) | set(seed):
        o, s = own.get(g), seed.get(g)
        if o is None or s is None:
            out[g] = o if s is None else s
            continue
        n = float(o.get("n", 0) or 0)
        wg = n / (n + K_GENRE)
        out[g] = {"bias": wg * float(o["bias"]) + (1.0 - wg) * float(s["bias"]),
                  "n": o["n"],   # the tenant's own count — a displayed fact, not a weight
                  "trust": wg * float(o["trust"]) + (1.0 - wg) * float(s["trust"])}
    return out


def _blend_upstream(own: dict, seed: dict, w: float) -> dict:
    """Blend pe.fit_upstream output (target -> {coef, drivers}) on the model-level
    weight. A target present in only one side, or fitted on a different driver set,
    is taken whole rather than mixing incomparable coefficient vectors."""
    out = dict(seed)
    for target, o in own.items():
        s = seed.get(target)
        if s is None or list(s.get("drivers", ())) != list(o.get("drivers", ())):
            out[target] = o
            continue
        out[target] = {
            "coef": w * np.asarray(o["coef"], dtype=float)
                    + (1.0 - w) * np.asarray(s["coef"], dtype=float),
            "drivers": o["drivers"],
        }
    return out


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
    before the load computes WA (see user_weights). A data-poor tenant shrinks
    toward the seed's fitted model (see SEED_USER_ID / _own_fit_weight)."""
    # Read-only scope: this whole build only SELECTs, so its connections run in
    # autocommit and each query costs one round trip instead of BEGIN + query. Same
    # SQL, same rows, same numbers — see db_backend.readonly(). Wrapping it HERE
    # rather than inside db_loader keeps the read-only engine file untouched.
    with db_backend.readonly():
        books, gw, gcw = db_loader.load_from_db(
            user_id=uid, weight_overrides=user_weights.load_overrides(uid))
    if len(books) == 0:
        books = _shape_empty_books(books, db_loader.CATEGORY_OF_INTEREST)

    if uid == SEED_USER_ID:
        # The seed never blends — it IS the prior. Byte-identical to pre-Phase-4.
        coeffs, r2, resid_sd = pe.fit_regression(books)
        ginfo = pe.genre_bias_and_trust(books, coeffs)
        upstream = pe.fit_upstream(books)
        return books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream

    w = _own_fit_weight(len(books))
    _, _, _, s_coeffs, s_r2, s_resid_sd, s_ginfo, s_upstream = _get_engine(SEED_USER_ID)
    if w <= 0.0:
        # Too few books to fit anything of their own: ride the prior whole.
        return books, gw, gcw, s_coeffs, s_r2, s_resid_sd, s_ginfo, s_upstream

    o_coeffs, o_r2, o_resid_sd = pe.fit_regression(books)
    coeffs = w * np.asarray(o_coeffs, dtype=float) + (1.0 - w) * np.asarray(s_coeffs, dtype=float)
    r2 = w * float(o_r2) + (1.0 - w) * float(s_r2)
    resid_sd = w * float(o_resid_sd) + (1.0 - w) * float(s_resid_sd)
    # Genre bias is recomputed against the BLENDED coeffs, not the tenant's own:
    # a bias term has to correct the regression that is actually going to be used.
    ginfo = _blend_ginfo(pe.genre_bias_and_trust(books, coeffs), s_ginfo)
    upstream = _blend_upstream(pe.fit_upstream(books), s_upstream, w)
    return books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream


def _get_engine(user_id=None) -> tuple:
    """The tenant's warm engine tuple, keyed on BOTH its own epoch and the SEED's.

    A non-seed tenant's tuple embeds a shrunken share of the seed's fitted model,
    so a write by the seed makes every other tenant's cached engine stale — under
    the v1 hard switch that only affected sub-threshold tenants, but under smooth
    shrinkage it reaches every tenant below the asymptote. Mirrors the two-epoch
    key _corr_statics already uses for the same reason. Values stay pure functions
    of committed DB state, so a miss just rebuilds the identical object."""
    uid = _uid(user_id)
    key = (_engine_epoch.get(uid, 0), _engine_epoch.get(SEED_USER_ID, 0))
    hit = _engine_cache.get(uid)
    if hit is not None and hit[0] == key:
        return hit[1]
    # SINGLE-FLIGHT. Every page fans out into several endpoint calls at once (the
    # Stats page alone makes three), and a write invalidates the cache for all of
    # them — so without this, N concurrent requests each ran a FULL rebuild of the
    # identical object, and since the rebuild is CPU-bound Python they serialised
    # on the GIL and each request waited for all N. Measured on the Stats page
    # right after an inline rating edit: three requests, three rebuilds, every one
    # of them blocked ~1470ms. One builder, the rest wait for its result.
    #
    # The token MUST include the uid. _build_engine_for recurses into
    # _get_engine(SEED_USER_ID) to blend a data-poor tenant toward the seed's
    # fitted model, and a fresh tenant's epoch key is (0, 0) — identical to the
    # seed's. Keyed on the epoch pair alone, that recursive call finds its OWN
    # token already present and the thread waits for itself, forever.
    token = (uid, key)
    with _engine_build_cv:
        while token in _engine_building:
            _engine_build_cv.wait()
            hit = _engine_cache.get(uid)          # the builder finished — reuse it
            if hit is not None and hit[0] == key:
                return hit[1]
        _engine_building.add(token)
    try:
        built = _build_engine_for(uid)
        _engine_cache[uid] = (key, built)
        return built
    finally:
        with _engine_build_cv:
            _engine_building.discard(token)
            _engine_build_cv.notify_all()


def _invalidate_engine(user_id=None) -> None:
    # LAZY (P2): drop the tenant's cached engine + derived caches so the NEXT read
    # rebuilds from the just-committed DB — instead of rebuilding synchronously in
    # the write request path. A burst of writes then costs ONE rebuild (on the next
    # read), not one per write. The rebuild is deterministic, so read-after-write is
    # correct. Startup warms the seed explicitly via _get_engine() (see lifespan).
    _invalidate_engine_local(_uid(user_id))
    # Tell the OTHER worker processes (see cache_sync). Local first, published
    # second, so this worker is already correct before anyone else is told — and
    # so a publish failure degrades to single-worker behaviour rather than to a
    # worker serving a library it knows is stale.
    cache_sync.publish("fiction", _uid(user_id))


def _invalidate_engine_local(uid) -> None:
    """Drop one tenant's fiction caches in THIS process only.

    The local half of _invalidate_engine, split out because cache_sync calls it for
    an invalidation that originated on another worker — where re-publishing would
    make two workers notify each other in a loop."""
    _engine_cache.pop(uid, None)
    _engine_epoch[uid] = _engine_epoch.get(uid, 0) + 1   # stale-keys _corr_statics
    _corr_statics_cache.pop(uid, None)
    # The cold-start term is NOT evicted here. It costs ~2s to fit (a full LOO pass
    # over the library), and evicting it charged that to the next reader — on a
    # single-process, GIL-bound server that stalled EVERY concurrent request, not
    # just the one that needed the term. The epoch bump above already marks the
    # cached term stale; _get_cold_term serves the previous fit and refreshes it off
    # the request path. See _cold_term_cache.


# Nonfiction engine cache — the (books, gw, gcw) tuple from the SEPARATE
# nonfiction engine, per tenant. Built lazily; rebuilt after any nonfiction write.
def _load_nf(uid) -> tuple:
    with db_backend.readonly():             # SELECT-only, see _build_engine_for
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
    # LAZY (P2), mirroring _invalidate_engine: drop → rebuild on next nonfiction read.
    _invalidate_nf_engine_local(_uid(user_id))
    cache_sync.publish("nf", _uid(user_id))


def _invalidate_nf_engine_local(uid) -> None:
    """The local half — see _invalidate_engine_local."""
    _nf_engine_cache.pop(uid, None)


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
# STALE-WHILE-REVALIDATE. The fit is a leave-one-out pass over the whole library
# (~140 correct_and_predict calls, ~2s), so it must never sit in a request path that
# a write has just invalidated. Each entry is (epoch, term): the _engine_epoch this
# term was fitted at, and the fitted coefs (or None — a legitimate "too few books to
# fit" result, which is why absence and None must stay distinguishable).
#
#   fresh (epoch matches)  -> serve it
#   stale (epoch moved on) -> serve it anyway, refit in the background
#   absent (never fitted)  -> fit synchronously; there is no previous value and one
#                             is never invented (same rule as the conformal interval)
#
# Consequence, accepted deliberately (owner decision, 2026-08-20): for the ~2s after
# a write, the served term is the one fitted on the library as it stood one write
# ago. It is an OLS slope over the whole library, so a single book moves it by a
# hair — and the alternative was a multi-second stall of the entire backend on the
# first read after every write.
_cold_term_cache: dict = {}
# One condition variable guards the cache, the in-flight set, and the wait/notify a
# first-ever fit uses to make concurrent callers share one fit instead of racing.
_cold_term_cv = threading.Condition()
_cold_term_refitting: set = set()          # uids with a refit in flight
# Single worker: the refit is CPU-bound Python, so widening this would only add GIL
# contention. Serializing refits across tenants is fine — each is best-effort and the
# stale value keeps serving until its own refit lands.
_cold_term_executor = _ThreadPoolExecutor(max_workers=1,
                                          thread_name_prefix="coldterm")
COLD_START_TERM_ENABLED = os.environ.get("COLD_START_TERM", "1") != "0"
# New-user favorite-author prior (Part B): a positive WA bump on the cold slice
# (n_author==0) when the unread book's author is a stated favorite (weight 1.0) or an
# LLM-found analog of one (discounted). Sanity-calibrated on the seed (favorite-author
# lift +0.5..+1.4; first-books-by-favorites under-predicted −0.66) → a conservative base.
_author_prior_cache: dict = {}          # normalized-favorites tuple → {base, map}
_AUTHOR_OFFSET_BASE = 0.5               # WA bump for a direct favorite
_ANALOG_WEIGHT = 0.5                    # analogs get this fraction of the favorite bump
# New-user favorite-GENRE prior: the genre analog of the author prior — a positive WA bump
# on the GENRE cold slice (n_genre==0) when the unread book's genre is a stated favorite.
# Same magnitude as the author favorite bump ("nudge like fav_authors"); genre is a broader
# bucket, so dial this down here if it reads as too strong. Direct favorites only — no LLM
# "analog genre" expansion (genre is already coarse; keeps this deterministic + API-free).
_genre_prior_cache: dict = {}           # normalized-fav-genres tuple → {base, map}
_GENRE_OFFSET_BASE = 0.5                # WA bump for a book in a favorite genre

# Star-derived genre prior (Workstream B, genre-only). When a reader has imported a
# Goodreads export we can do better than their five self-reported favorites: shrunken
# per-genre offsets computed from their own star ratings (star_priors.py), filling the
# SAME genre_prior slot. Graded instead of binary, covers every genre they've read
# instead of five, and — the substantive difference — SIGNED, so a genre they rate below
# their own average pushes a cold-slice prediction DOWN.
#
# The per-AUTHOR equivalent was measured and REJECTED (2026-08-08): ~9% to-read coverage
# vs genre's ~91%. Don't reintroduce it without new coverage evidence.
#
# Kill switch: STAR_GENRE_PRIOR=0 falls straight back to the favorites prior. With no
# stored offsets this path is inert, so every existing tenant is byte-identical.
STAR_GENRE_PRIOR_ENABLED = os.environ.get("STAR_GENRE_PRIOR", "1") != "0"
# Metadata key written at import-commit; read on every predict via user_metadata.
STAR_GENRE_OFFSETS_KEY = "genre_offsets"


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


def _expand_genre_prior(favs):
    """Build {base, map} from favorite genre names (each weight 1.0). The genre analog of
    _expand_author_prior, minus the LLM analog widening — genre is already a coarse bucket,
    so favorites alone keep the nudge deterministic and API-free. Empty input → None."""
    m = {}
    for g in favs:
        ng = _rp.normalize_genre(g)
        if ng:
            m[ng] = 1.0
    if not m:
        return None
    return {"base": _GENRE_OFFSET_BASE, "map": m}


def _build_genre_prior(fav_genres, genre_offsets=None):
    """The genre_prior slot: star-derived offsets when the reader has imported, else their
    self-reported favorites.

    REPLACE, never stack. Both fill the same slot, so adding them would double-count a genre
    that is both a stated favorite AND highly rated — and the star offsets are strictly better
    evidence for exactly the thing the favorites list was a proxy for. Favorites remain the
    fallback for readers who never import.

    Star offsets are NOT cached in _genre_prior_cache: that cache is keyed by the favorites
    tuple, which says nothing about a tenant's offsets. The star path is a cheap dict
    comprehension over ~10 genres, so it just rebuilds per call."""
    if STAR_GENRE_PRIOR_ENABLED and genre_offsets and _rp is not None:
        gp = star_priors.to_genre_prior(genre_offsets, _rp.normalize_genre)
        if gp:
            return gp
    favs = tuple(str(g).strip() for g in (fav_genres or []) if str(g).strip())[:5]
    if not favs:
        return None
    if favs not in _genre_prior_cache:
        _genre_prior_cache[favs] = _expand_genre_prior(favs)
    return _genre_prior_cache[favs]


def _fitted_cold_term(uid):
    """The tenant's fitted word-count term, stale-while-revalidate (see _cold_term_cache).

    Fits synchronously ONLY when the tenant has no previously fitted term at all;
    otherwise the stored value is served immediately and a refit is scheduled off the
    request path. Refits are single-flighted per tenant, so a page that fans out to
    several cold-term endpoints at once triggers one fit, not one per request."""
    want = _engine_epoch.get(uid, 0)
    with _cold_term_cv:
        entry = _cold_term_cache.get(uid)
        if entry is not None and entry[0] == want:
            return entry[1]                          # fresh
        if entry is not None:                        # stale: serve it, refresh behind
            schedule = uid not in _cold_term_refitting
            if schedule:
                _cold_term_refitting.add(uid)
            if schedule:
                try:
                    _cold_term_executor.submit(_refit_cold_term, uid)
                except Exception:                    # executor shut down / rejected
                    _cold_term_refitting.discard(uid)
            return entry[1]
        # Never fitted for this tenant. There is nothing honest to serve but the real
        # thing, so fit it here — but only once: a concurrent first-request waits for
        # the in-flight fit rather than starting a second one.
        while uid in _cold_term_refitting:
            _cold_term_cv.wait()
            entry = _cold_term_cache.get(uid)
            if entry is not None:
                return entry[1]
        _cold_term_refitting.add(uid)
    return _refit_cold_term(uid)


def _refit_cold_term(uid):
    """Fit the term for `uid`, store it against the epoch it was fitted at, and return it.

    Reads the epoch BEFORE fitting: if a write lands mid-fit, the result is stored as
    belonging to the older epoch, so the next read sees it as stale and schedules another
    refit rather than pinning a value that never saw the newer data. A failed fit never
    clobbers a good previous value — the tenant keeps serving the older term."""
    at = _engine_epoch.get(uid, 0)
    failed = False
    try:
        term = _fit_cold_term_for(uid)               # fitted coefs, or None (too few books)
    except Exception:
        logging.getLogger(__name__).warning(
            "cold-start term refit failed for a tenant", exc_info=True)
        term, failed = None, True
    with _cold_term_cv:
        prev = _cold_term_cache.get(uid)
        if not (failed and prev is not None):
            _cold_term_cache[uid] = (at, term)
        _cold_term_refitting.discard(uid)
        _cold_term_cv.notify_all()
        return _cold_term_cache.get(uid, (at, term))[1]


def _get_cold_term(user_id=None, word_count_pref=None, fav_authors=None, fav_genres=None,
                   genre_offsets=None):
    """Per-tenant cold-start term — INDEPENDENT components, each applied only on its own
    cold slice by correct_and_predict:
      * word count (AUTHOR slice, n_author==0): the tenant's FITTED slope once they have
        enough books, else their onboarding word-count preference (new users);
      * author prior (AUTHOR slice, n_author==0): favorite authors + analogs, attached
        whenever set. It fades PER AUTHOR (the moment you rate that author), NOT with library
        size — so a favorite you still haven't read keeps its nudge even once you're data-rich;
      * genre prior (GENRE slice, n_genre==0): the reader's STAR-DERIVED per-genre offsets
        when they've imported a Goodreads export, else their stated favorite genres — see
        _build_genre_prior. Attached whenever set; fades PER GENRE (the moment you rate a
        book in that genre), independent of the author gate.
    None when no component applies."""
    if not COLD_START_TERM_ENABLED or _rp is None:
        return None
    uid = _uid(user_id)
    fitted = _fitted_cold_term(uid)
    # Word-count component: fitted (data-rich) else the stated preference (new user).
    # dict(...) copies so attaching a prior never mutates the cached fitted term.
    term = dict(fitted if fitted is not None
                else (_preference_cold_term(word_count_pref) or {}))
    ap = _build_author_prior(fav_authors)                   # independent of library size
    if ap:
        term["author_prior"] = ap
    gp = _build_genre_prior(fav_genres, genre_offsets)      # independent of library size
    if gp:
        term["genre_prior"] = gp
    return term or None                                     # {} → nothing to apply


def _refresh_genre_offsets(user_id, user_md):
    """Recompute the reader's star-derived per-genre offsets from their staged `read`
    rows and persist them to Supabase user_metadata. Returns the count stored (0 when
    nothing qualified or the write didn't stick).

    Best-effort on purpose: this is an enrichment on top of a commit that has ALREADY
    succeeded, so any failure here is swallowed rather than surfaced — a reader must
    never see their import fail because a prior couldn't be saved. They simply keep the
    favorites-based prior.

    Recomputes from scratch rather than merging, so re-importing or fixing genres in
    review converges instead of compounding. A later commit sees fewer `read` rows (each
    one is deleted as its book gets ranked), which is fine and self-correcting: a genre
    the reader has actually rated has n_genre > 0, so the star prior is already gated off
    for it.

    TIMING: auth.get_current_user_metadata reads the JWT CLAIMS, not the database — so a
    freshly-written offset takes effect on the reader's next token refresh, not on their
    next request. That suits this feature (importing then ranking a backlog spans far
    longer than a token lifetime) but it does mean the prior is not instantaneous. Nothing
    to invalidate locally: _cold_term_cache holds only the fitted word-count term, while
    the priors are rebuilt per call from whatever metadata the request carries."""
    if not STAR_GENRE_PRIOR_ENABLED:
        return 0
    try:
        rows = db_write.get_staging_rows(user_id, shelf="read", limit=10 ** 9)
        offsets = star_priors.genre_offsets(rows)
        if not offsets:
            return 0
        ok = signup_mod.set_user_metadata(
            user_id, {STAR_GENRE_OFFSETS_KEY: offsets}, existing=user_md)
        return len(offsets) if ok else 0
    except Exception:
        logging.getLogger(__name__).warning(
            "genre-offset refresh failed for a tenant", exc_info=True)
        return 0


def _cold_adjust_rec_wa(wa, words, series_number, author, genre, n_author, n_genre, cold_term):
    """Apply the cold-start term to a SAVED recommendation's displayed WA so cold-slice
    recs rank consistently with the live Predict page. No-op unless the reader has a term and
    the rec sits on at least one cold slice — no same-author analog (n_author == 0) and/or no
    same-genre analog (n_genre == 0) — the same per-component gates correct_and_predict uses.
    Keeps the read-queue and reading-status slots agreeing on the same book's WA."""
    if cold_term is None or _rp is None or (n_author != 0 and n_genre != 0):
        return wa
    return _rp.apply_cold_start_term(wa, words, series_number, author, genre,
                                     n_author, n_genre, cold_term)


def _correction_pool(user_id, books_e):
    """Training pool for the research-path author+genre correction. A tenant with too few
    books to fit their own model would otherwise correct against a tiny/empty library —
    which is degenerate (near-raw), noisy (a handful of idiosyncratic ratings swing the
    prediction wildly), or an outright crash on an empty pool. So a below-threshold tenant
    borrows the SEED's calibrated books UNIONed with their own (their reads still add
    analogs; the seed's 129 dominate the calibration). This completes the model-level
    cold-start prior in _build_engine_for for the research path. The seed and any
    data-rich tenant use their own books unchanged, byte-identical predictions.

    NOTE: this is still a HARD switch at MIN_OWN_FIT, deliberately — _build_engine_for
    blends fitted PARAMETERS, which mix linearly; a training POOL does not, so the same
    smooth ramp does not transfer here. Softening it (e.g. sample-weighting the seed rows
    by 1 - _own_fit_weight) is a separate piece of work with its own accuracy gate."""
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
_corr_statics_cache = _LRUCache(_TENANT_CACHE_MAX)   # uid -> (key, pairs, corr_models)
_engine_epoch: dict = {}         # uid -> int, bumped by _invalidate_engine (NOT capped)


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


def _apply_remote_invalidation(scope, user_id) -> None:
    """cache_sync callback: another worker wrote, so drop this process's caches for
    that tenant. LOCAL ONLY — it must never publish, or workers would ping-pong."""
    if scope == "nf":
        _invalidate_nf_engine_local(user_id)
    else:
        _invalidate_engine_local(user_id)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    _get_engine()  # warm the seed engine at startup (build + cache; invalidate is now lazy)
    # Warm the seed's cold-start term off the request path too. Without this the first
    # reader after a deploy pays the whole ~2s fit synchronously (there is no previous
    # value to serve), which is exactly the stall the stale-while-revalidate cache
    # exists to avoid. Best-effort: a failure here just restores the old behaviour.
    if COLD_START_TERM_ENABLED and _rp is not None:
        try:
            _cold_term_executor.submit(_fitted_cold_term, _uid(None))
        except Exception:
            pass
    # Listen for writes handled by OTHER worker processes. No-op on SQLite/local
    # dev (one process, nothing to synchronise) — see cache_sync.
    try:
        if cache_sync.start(_apply_remote_invalidation):
            logging.getLogger("uvicorn.error").info(
                "cache_sync: cross-process invalidation active (worker %s)",
                cache_sync.WORKER_ID)
    except Exception:
        logging.getLogger("uvicorn.error").warning(
            "cache_sync failed to start; caches are per-process only",
            exc_info=True)
    yield


# API docs (/docs, /redoc, /openapi.json) disclose the full endpoint schema. Keep
# them on for local dev, but OFF on the hosted app (AUTH_ENABLED=1) so an
# unauthenticated caller can't enumerate the API. EXPOSE_DOCS=1 forces them back on
# (e.g. to debug a deploy). Deploy verification uses the token-free /api/version
# route, which is unaffected by this.
_EXPOSE_DOCS = (not auth.AUTH_ENABLED) or os.environ.get("EXPOSE_DOCS", "0").strip() == "1"
app = FastAPI(
    title="Reading Ledger API", version="1.0", lifespan=lifespan,
    docs_url="/docs" if _EXPOSE_DOCS else None,
    redoc_url="/redoc" if _EXPOSE_DOCS else None,
    openapi_url="/openapi.json" if _EXPOSE_DOCS else None,
)

# Safe defaults: localhost only. Override via env vars only for deliberate,
# network-aware deployments that have also added authentication.
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:3000")
_BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")  # informational; enforced by uvicorn CLI

log = logging.getLogger("reading_ledger")


# ─────────────────────────────────────────────────────────────────────────────
# Unhandled-exception net — MUST be registered BEFORE CORSMiddleware below.
# ─────────────────────────────────────────────────────────────────────────────
# Starlette's own ServerErrorMiddleware sits OUTSIDE every user middleware, so a
# 500 it generates never passes back through CORSMiddleware and goes out with no
# Access-Control-Allow-Origin header. A browser cannot read such a response: it
# reports the request as a network failure — "TypeError: Failed to fetch" — with
# no status and no message. The real error is invisible to the client, and to
# anyone debugging from one.
#
# Registering this first makes CORSMiddleware the OUTER layer (add_middleware
# builds the stack so the LAST registered wraps the earlier ones), so the
# JSONResponse below is a normal response travelling out through CORS and picks
# up the headers. The client then sees an honest 500 with a message instead of a
# phantom connectivity problem.
#
# It changes no successful response, and leaks nothing: the detail is generic and
# the traceback goes to the server log (same discipline as _server_error).
@app.middleware("http")
async def _cors_safe_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong on the server. "
                                "The error has been logged."},
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _server_error(exc: Exception, context: str = "") -> HTTPException:
    """Map an unexpected exception to a safe HTTPException — raw exception text and
    stack traces never reach the client. Use for the `except Exception` fallbacks;
    keep raising `ValidationError` as a 422 with its own (safe, user-facing) message.

    One condition is surfaced honestly rather than as a generic 500: an Anthropic
    credit-balance 400 is a known, non-secret billing state (not a bug), so it
    becomes a clear 503 — like the missing-key 503 — telling the operator to top
    up, and is logged as a concise warning instead of a full-traceback error."""
    if _rl is not None and _rl.is_out_of_credits(exc):
        log.warning("Anthropic credit balance exhausted%s",
                    f" in {context}" if context else "")
        return HTTPException(
            status_code=503,
            detail="The prediction service is temporarily out of Anthropic API "
                   "credits. Top up in the Anthropic Console (Plans & Billing), "
                   "then try again.")
    log.exception("Unhandled error%s", f" in {context}" if context else "")
    return HTTPException(status_code=500, detail="Internal server error.")


# ── Rate limiting ────────────────────────────────────────────────────────────
# A minimal in-memory sliding-window limiter (no new dependency; matches the
# stdlib-only ethos of signup.py). It protects the money/abuse-sensitive routes:
# unauthenticated sign-up (invite-code brute force) and the LLM-backed endpoints
# (each grounded call spends Anthropic credits). State is per-process, so it fits
# the single-worker Railway deploy and resets on restart — good enough as an abuse
# brake, not a distributed quota.
#
# KEYING (the load-bearing security detail):
#   * LLM routes are auth-gated, so every caller is a known tenant — we key by the
#     verified user_id (token `sub`). A per-tenant budget cannot be evaded by
#     rotating source IPs and isolates one heavy user from everyone else.
#   * Sign-up is pre-auth (no user_id yet) — we key by the real client IP.
#   * The real client IP behind Railway's Envoy edge is X-Envoy-External-Address (a
#     single value the edge controls), NOT the left-most X-Forwarded-For hop: the
#     edge APPENDS the true IP to the RIGHT of any client-supplied XFF, so the
#     left-most hop is attacker-controlled. Keying on it would let a spoofed,
#     rotating XFF bypass the limit entirely (see _client_ip).
_RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "1").strip() != "0"
_rl_lock = threading.Lock()
_rl_hits: dict = defaultdict(deque)
_rl_calls = 0  # opportunistic-GC tick counter (see _rate_limit)


def _client_ip(request: Request) -> str:
    """Best-effort real client IP behind Railway's Envoy edge proxy.

    Trust order: X-Envoy-External-Address (edge-set, single trusted value) →
    the RIGHT-most X-Forwarded-For hop (the one the edge appended; left-most hops
    are client-supplied and spoofable) → the socket peer (local dev, no proxy)."""
    envoy = request.headers.get("x-envoy-external-address", "").strip()
    if envoy:
        return envoy
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def _rate_limit(request: Request, bucket: str, max_calls: int, window_s: float,
                user_id: Optional[str] = None, raise_on_limit: bool = True) -> bool:
    """Allow at most `max_calls` in the last `window_s` seconds per (bucket,
    principal); raise 429 with Retry-After otherwise. No-op when
    RATE_LIMIT_ENABLED=0. The principal is the verified `user_id` when supplied
    (auth-gated routes) else the real client IP (pre-auth routes like sign-up).

    Returns True when the call is within budget (and records it), False when it is
    over budget. `raise_on_limit` (the default) turns an over-budget call into an
    HTTP 429 — so every existing caller, which ignores the return value, is
    unchanged. Pass raise_on_limit=False to gate a request GRACEFULLY (e.g. the
    public demo falling back to a cached-only response instead of erroring)."""
    if not _RATE_LIMIT_ENABLED:
        return True
    principal = f"user:{user_id}" if user_id else f"ip:{_client_ip(request)}"

    # SHARED buckets go to the database so the budget is the deployment's, not each
    # worker's. Only the money gates qualify (see _SHARED_BUCKETS): they are
    # low-volume and each guards a multi-second paid API call, so a round trip is
    # free next to what it protects. Everything else stays in memory below with its
    # budget divided by the worker count.
    if bucket in _SHARED_BUCKETS and _shared_state():
        try:
            if db_write.rate_limit_try(bucket, principal, max_calls, window_s):
                return True
        except Exception:
            # The limiter must never take the endpoint down with it. Fall through to
            # the in-process bucket, which still caps this worker.
            logging.getLogger(__name__).warning(
                "shared rate-limit check failed for %s; using the local bucket",
                bucket, exc_info=True)
        else:
            if not raise_on_limit:
                return False
            try:
                retry = db_write.rate_limit_retry_after(bucket, principal, window_s)
            except Exception:
                retry = int(window_s) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please slow down and try again shortly.",
                headers={"Retry-After": str(retry)},
            )

    key = f"{bucket}:{principal}"
    now = time.monotonic()
    max_calls = _local_budget(bucket, max_calls)
    with _rl_lock:
        dq = _rl_hits[key]
        cutoff = now - window_s
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= max_calls:
            if not raise_on_limit:
                return False
            retry = int(window_s - (now - dq[0])) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please slow down and try again shortly.",
                headers={"Retry-After": str(retry)},
            )
        dq.append(now)
        # Opportunistic GC so keys for IPs/tenants that never return don't
        # accumulate forever. Every _RL_GC_EVERY calls, drop entries older than
        # the LARGEST window (safe for every bucket) and delete now-empty keys.
        global _rl_calls
        _rl_calls += 1
        if _rl_calls % _RL_GC_EVERY == 0:
            dead = now - _RL_MAX_WINDOW
            for k in list(_rl_hits.keys()):
                d = _rl_hits[k]
                while d and d[0] <= dead:
                    d.popleft()
                if not d:
                    del _rl_hits[k]
    return True


# Buckets whose budget is enforced in the DATABASE rather than per worker process.
# The rule is "does exceeding it cost money": each of these gates a paid Anthropic
# call, so N workers each honouring the full budget would mean up to N times the
# spend. They are also the cheapest to share — the demo's daily cap is a few dozen
# rows a day, and every one of them sits in front of a multi-second API call, so the
# round trip is not measurable. `signup` joins them for the same reason in kind: it
# is a security gate on account creation, a handful of calls ever, and an N-times
# looser one is not something to accept for free. Read-path buckets are deliberately
# NOT here: a DB write on every list request would spend exactly the latency this
# release recovers.
_SHARED_BUCKETS = frozenset({"llm", "demo_live", "demo_live_global", "signup"})


def _shared_state():
    """True when this deployment actually has shared storage to coordinate through.

    Gates every write to the `app_state` / `rate_limit_hits` tables. It is not just
    an optimisation: on local dev the database IS the tracked `books.db`, so a
    rate-limit row per LLM call would churn the file that the autopublish watcher
    commits and the static snapshot is built from. Local dev is one process and has
    nothing to share, so it takes the in-memory path exactly as it always did."""
    return cache_sync.enabled()


def _local_budget(bucket, max_calls):
    """Divide an in-process budget by the worker count so the AGGREGATE cap across
    workers stays what it says on the tin.

    The trade is visible and worth naming: a client that happens to keep landing on
    the same worker gets throttled at its share rather than the whole budget. That
    is the right way round for these buckets (they guard cheap reads, and the
    budgets are generous), but it is an approximation, not an exact cap — which is
    precisely why the money buckets went to the database instead. Never divides
    below 1."""
    if WORKERS <= 1 or bucket in _SHARED_BUCKETS:
        return max_calls
    return max(1, int(max_calls) // WORKERS)


# Per-route budgets. LLM routes are generous enough for a real session (predicting
# a series fans out client-side into several calls) but cap runaway/abusive loops.
_RL_LLM = dict(max_calls=40, window_s=60.0)
_RL_SIGNUP = dict(max_calls=5, window_s=300.0)
# Public-profile / cross-user browse (a NEW outside-facing read surface, so it
# gets its own bucket even though the other data GETs are unthrottled). Keyed on
# the VIEWER's user_id — a profile page load fans out into several endpoint calls,
# so the budget is generous but caps a scraper enumerating other tenants.
_RL_PROFILE = dict(max_calls=90, window_s=60.0)
# Goodreads import (onboarding). An upload parses a whole file; review edits are
# light. Auth-gated + keyed on the caller's user_id; generous but caps a runaway
# client or an abusive upload loop.
_RL_IMPORT = dict(max_calls=60, window_s=60.0)
# Public "Try it" demo (UNAUTHENTICATED → keyed by client IP). A CACHED prediction
# is engine-only (effectively free), so the overall per-IP bucket is generous; a
# LIVE (cache-miss) prediction spends one Anthropic call, so it is gated TWICE — a
# tight per-IP hourly bucket AND a single GLOBAL daily cap (one shared principal)
# that bounds total spend no matter how many IPs appear. All three are env-tunable
# without a code change; DEMO_LIVE_DAILY_CAP=0 disables the live path entirely.
_RL_DEMO = dict(max_calls=int(os.environ.get("DEMO_RL_MAX", "30")), window_s=60.0)
_RL_DEMO_LIVE = dict(
    max_calls=int(os.environ.get("DEMO_LIVE_IP_PER_HOUR", "5")), window_s=3600.0)
_DEMO_LIVE_DAILY_CAP = int(os.environ.get("DEMO_LIVE_DAILY_CAP", "50"))
_DEMO_LIVE_WINDOW_S = 86400.0
_DEMO_GLOBAL_PRINCIPAL = "demo:global"   # one shared bucket → a true global cap
# GC tuning: sweep every N limiter calls; never prune an entry younger than the
# largest window (else a sweep triggered by a short-window bucket would wrongly
# expire a long-window bucket's entries).
_RL_GC_EVERY = 500
_RL_MAX_WINDOW = max(_RL_LLM["window_s"], _RL_SIGNUP["window_s"],
                     _RL_PROFILE["window_s"], _RL_IMPORT["window_s"],
                     _RL_DEMO["window_s"],
                     _RL_DEMO_LIVE["window_s"], _DEMO_LIVE_WINDOW_S)


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
    con = db_backend.connect(db_write.DB, readonly=True)
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
    con = db_backend.connect(db_write.DB, readonly=True)
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
def get_genres(user_id: str = Depends(auth.get_current_user_id)):
    """Distinct genres in the CALLER's rated library. Auth-gated + tenant-scoped
    (S2): it was unauthenticated and served the seed library's genres to anyone."""
    books = _get_engine(user_id)[0]
    return sorted(books["Genre"].dropna().unique().tolist())


@app.get("/api/valid-genres")
def get_valid_genres(user_id: str = Depends(auth.get_current_user_id)):
    """Genres valid for adding a book: the global genre_weights set PLUS the
    caller's own private genres."""
    con = db_backend.connect(db_write.DB, readonly=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# SCORE ANCHORS  (the reader's prose→number rating scale)
# ─────────────────────────────────────────────────────────────────────────────
# Grounded research turns what reviewers SAY about a book into 0-10 component
# scores through a fixed sentiment table ("really strong / recommend it" → 8.0-8.5).
# These routes let each tenant set their own number for each of the seven bands;
# score_anchors applies them as a monotone remap of the raw research vector, before
# the engine's corrections. No engine invalidation is needed — the anchors touch the
# RESEARCH input, not the weights the cached engine is built from, so they take
# effect on the next prediction. Books already predicted keep their stored scores
# until they're re-predicted (same as a weights change re-ranking, not re-scoring).
class ScoreAnchorsRequest(BaseModel):
    anchors: dict[str, float]          # every band key -> its 0-10 centre


@app.get("/api/score-anchors")
def get_score_anchors(user_id: str = Depends(auth.get_current_user_id)):
    """The caller's EFFECTIVE rating-scale anchors: one row per sentiment band
    (label, canonical range, default, their value) plus a `customized` flag, for
    the anchor editor in the first-run tutorial. Read-only."""
    return sa.effective_anchors(user_id)


@app.put("/api/score-anchors")
def put_score_anchors(req: ScoreAnchorsRequest,
                      user_id: str = Depends(auth.get_current_user_id)):
    """Set the caller's rating-scale anchors. Must supply every band, each a
    number in 0-10, nondecreasing from "bad / DNF" up to "best in genre" (an
    inversion would reorder books rather than re-price them)."""
    if not db_write.set_score_anchors(req.anchors, user_id=user_id):
        raise HTTPException(
            status_code=422,
            detail="Could not save your rating scale. Give every band a number "
                   "between 0 and 10, rising from the weakest band to the best.")
    return {"ok": True}


@app.post("/api/score-anchors/reset")
def post_reset_score_anchors(user_id: str = Depends(auth.get_current_user_id)):
    """Revert the caller to the canonical anchor table (the identity remap)."""
    db_write.reset_score_anchors(user_id=user_id)
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
        raise _server_error(e)
    out = buf.getvalue().strip()
    if "✗" in out:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not add book.")

    # Remove the finished book from the queue so slots advance automatically.
    try:
        con = db_backend.connect(db_write.DB, readonly=True)
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
        con = db_backend.connect(db_write.DB, readonly=True)
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
    _record_repredict_report(token, user_id, report)


def _submit_background_ground(title: str, author: str, genre: str, user_id: str) -> None:
    """Enqueue after-save grounding for one saved rec onto the dedicated grounding
    executor (bounded width = rate-limit politeness). Non-blocking best-effort: a
    disabled feature, missing deps, or a full/rejected submit just leaves the rec
    memory-scored (the user can still refine it manually). Never raises into the
    request path."""
    if _ground_executor is None or _repred is None or _rp is None:
        return
    try:
        _ground_executor.submit(_run_background_ground, title, author, genre, user_id)
    except Exception:
        pass


def _run_background_ground(title: str, author: str, genre: str, user_id: str) -> None:
    """Grounding-executor worker: upgrade one saved rec's stored scores from memory
    to the grounded prediction, using the tenant's CACHED engine + per-run statics
    (so no rebuild per book). Writes are serialized on _repred_lock against other
    background writers (repredict / other groundings) so the SQLite writer never
    contends. The lock is threaded into ground_saved_rec and held ONLY around its
    write — never around the slow web_search — so the executor's 3 workers still
    ground in parallel. Fully best-effort — a failure just leaves the rec
    memory-scored."""
    try:
        engine = _get_engine(user_id)
        books_e = engine[0]
        corr_pool = _correction_pool(user_id, books_e)
        pairs, corr_models = _corr_statics(user_id, corr_pool)
        res = _repred.ground_saved_rec(
            title, author, genre,
            get_engine=lambda: (corr_pool,) + tuple(engine[1:]),
            corr_models=corr_models, pairs=pairs, user_id=user_id,
            write_lock=_repred_lock)
        if res and res.get("changed"):
            print(f"  (background-ground '{title}': WA {res.get('old_wa')}→{res.get('new_wa')})")
    except Exception as exc:
        print(f"  (background-ground failed for '{title}': {exc})")


@app.post("/api/recommendations/{title}/repredict")
def repredict_recommendation(title: str, request: Request,
                             user_id: str = Depends(auth.get_current_user_id),
                             user_md: dict = Depends(auth.get_current_user_metadata)):
    """GRANULAR re-prediction: re-predict ONE unread recommendation, on demand.

    The counterpart to the automatic cohort pass above. Finishing a book sweeps
    every peer whose baseline moved; this re-predicts exactly the book the reader
    pointed at, against the library as it stands right now — nothing else in the
    TBR is touched.

    SYNCHRONOUS and potentially slow (up to a couple of minutes) when the book
    has never been web-grounded: it runs the same live path as /api/predict/research,
    so it carries the same LLM rate-limit bucket. A warm book is engine-only and
    returns in milliseconds. Deliberately not backgrounded — the reader asked for
    this one book by name and is waiting on its answer, unlike the on-add cohort
    pass whose whole point is that the add returns instantly.

    Fiction only (the nonfiction track has its own table and engine). Returns the
    old→new report; 404 if the title is not on this reader's active TBR."""
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _repred is None or _rp is None:
        raise HTTPException(status_code=503,
                            detail="Re-prediction is unavailable on this deployment.")
    try:
        engine = _get_engine(user_id)
        books_e = engine[0]
        corr_pool = _correction_pool(user_id, books_e)
        pairs, corr_models = _corr_statics(user_id, corr_pool)
    except Exception as e:
        raise _server_error(e, "Engine build failed")

    # books = the (possibly seed-borrowed) CORRECTION pool; rank_pool = the
    # reader's OWN library, so a cold-start reader is never ranked against the
    # seed corpus. cold_term is passed so the reported WA is the one the reader
    # sees on the read-queue and Predict page for this book rather than the raw
    # correction output — same term, same gate as _cold_adjust_rec_wa.
    # _repred_lock is threaded in as the WRITE lock only — it is never held
    # across the slow web call, so this can't stall the background grounding
    # executor.
    report = _repred.repredict_one(
        title,
        get_engine=lambda: (corr_pool,) + tuple(engine[1:]),
        rank_pool=books_e, corr_models=corr_models, pairs=pairs,
        cold_term=_get_cold_term(user_id, user_md.get("word_count_pref"),
                                 user_md.get("fav_authors"),
                                 user_md.get("fav_genres"),
                                 user_md.get(STAR_GENRE_OFFSETS_KEY)),
        user_id=user_id, write_lock=_repred_lock)
    if report is None:
        raise HTTPException(status_code=404,
                            detail=f"No unread book titled '{title}' on your list.")
    if report.get("skipped"):
        raise HTTPException(
            status_code=422,
            detail="Could not re-predict this book — no research vector available "
                   f"({report['skipped']}).")
    # A moved prediction that failed to persist is an error, not a result — surface
    # it rather than letting the client render it as an unchanged re-prediction.
    if report.get("changed") and not report.get("written"):
        raise HTTPException(status_code=500,
                            detail="Re-predicted, but the new scores could not be saved.")
    return {"ok": True, "report": report}


# The add-book panel polls for its background re-prediction report by token. The
# report used to live in a module-level dict in repredict_on_add, which under
# `--workers` is per PROCESS: the POST that started the work and the GET that polls
# for it land on whichever worker the load balancer picks, so the poll would miss
# roughly (N-1)/N of the time and the panel would silently time out.
#
# So the report is written to the shared `app_state` table, keyed by the token AND
# the tenant — the token alone would let anyone who guessed one read another
# reader's report. The in-process store is still written as a fallback, which is
# what serves local dev and any deployment where the shared write fails.
#
# 15 minutes is far longer than the client polls (it gives up in well under a
# minute) and short enough that these rows never accumulate; the sweeper in
# cache_sync clears expired ones.
_REPREDICT_REPORT_TTL_S = 900


def _repredict_report_key(token: str, user_id: str) -> str:
    return f"repredict:{user_id}:{token}"


def _record_repredict_report(token: str, user_id: str, report: dict) -> None:
    """Stash a finished report where ANY worker can serve it. Best-effort on the
    shared write — the local store always gets it, so a DB hiccup costs the panel
    only when the poll lands on a different worker."""
    _repred.record_report(token, report)
    if not _shared_state():
        return                      # one process: the local store IS the store
    try:
        db_write.app_state_put(_repredict_report_key(token, user_id),
                               json.dumps(report), ttl_s=_REPREDICT_REPORT_TTL_S)
    except Exception:
        logging.getLogger(__name__).warning(
            "could not share a re-prediction report across workers", exc_info=True)


def _read_repredict_report(token: str, user_id: str):
    """The report for `token`, or None while it is still pending. Checks this
    worker's own memory first (free, and the common case when there is one worker),
    then the shared table (only when there is one — see _shared_state)."""
    report = _repred.get_report(token)
    if report is not None or not _shared_state():
        return report
    try:
        raw = db_write.app_state_get(_repredict_report_key(token, user_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


@app.get("/api/repredict/recent")
def repredict_recent(token: str, user_id: str = Depends(auth.get_current_user_id)):
    """Poll for a background cohort re-prediction's report by its token. Returns
    {status:"pending"} until the background pass finishes, then {status:"done",
    report:{...}}. Never 404s — a token that never existed just stays pending
    (the client stops polling on its own timeout)."""
    if _repred is None:
        return {"status": "done", "report": None}
    report = _read_repredict_report(token, user_id)
    if report is None:
        return {"status": "pending"}
    return {"status": "done", "report": report}


def _maybe_log_delta(title: str, act_scores: dict, user_id: str) -> None:
    """Check recommendations for a stored prediction and log delta if found.
    Tenant-scoped: only the caller's own prediction row is matched and logged."""
    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e)
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
        raise _server_error(e)
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


# ── Input hygiene for user-typed text bound for LLM prompts / cache keys (S3) ──
# Strip control characters and cap length before a title/author/free-text query
# reaches an LLM prompt or a cache key: this bounds the prompt-injection and
# token-waste surface. NOT a complete injection defense on its own — the engine
# also clamps its numeric outputs to [0,10] — but it removes the structural-break
# vectors (newlines/control chars) and unbounded payloads. Idempotent: a normal
# title, an accented or non-Latin title, and an author name are returned
# unchanged; None is preserved for Optional fields. Applied at the Pydantic layer
# (below) so every LLM-facing request is sanitized at parse time — no call site
# can forget it. This normalizes inputs only; it never changes prediction math.
def _sanitize_user_text(s, max_len=200):
    if s is None:
        return s
    s = "".join(c if c.isprintable() else " " for c in str(s))
    return " ".join(s.split())[:max_len]


_CleanText = Annotated[str, AfterValidator(lambda v: _sanitize_user_text(v, 200))]
_CleanQuery = Annotated[str, AfterValidator(lambda v: _sanitize_user_text(v, 500))]


class LookupRequest(BaseModel):
    title: _CleanText
    author_hint: Optional[_CleanText] = None


def _lookup_from_prediction(title: str, user_id: str) -> Optional[dict]:
    """If this title has already been predicted (it exists in the caller's
    recommendations), return its stored metadata in the /api/lookup shape so the
    lookup can skip the LLM entirely — no API key, no spend. Every field the
    lookup surfaces (author/genre/words/series/series_number/blurb) is persisted
    on the recommendation at save time, so nothing is re-derived. Tenant-scoped,
    with the same case-insensitive/trimmed title match _maybe_log_delta uses, so
    the canonical stored title flows back and the eventual add re-finds the
    prediction. Returns None when no prediction is on record."""
    con = db_backend.connect(db_write.DB, readonly=True)
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
def lookup_book(req: LookupRequest, request: Request,
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

    # Only the LLM path is metered — cached lookups above are free.
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)

    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")

    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise _server_error(e, "lookup: LLM client init")

    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e, "lookup")


@app.get("/api/tiers")
def get_tiers(year: Optional[int] = None, years: Optional[str] = None,
              user_id: str = Depends(auth.get_current_user_id)):
    """Books with tier assignments (S+/S/A/B/C/D/F), optionally filtered by year_read.

    `years=all` additionally returns a `by_year` map holding the same payload for
    every year the reader has. Tier bands are computed within a year's cohort, so
    the Tier List page needs one payload per year — and it could only learn WHICH
    years exist from this response, making the per-year fetches a second, dependent
    round trip to a database ~50ms away. One call now answers both questions.

    Omitted by default, so the static snapshot and every other caller keep the exact
    response they had; the static build has per-year files and reads them off local
    disk, where a second hop costs nothing."""
    books = _get_engine(user_id)[0]
    category_components = books.attrs["category_components"]
    snum_map = _series_number_map("books", user_id)

    payload = _tier_payload(books, category_components, snum_map, year)
    if year is None and (years or "").strip().lower() == "all":
        present = sorted({int(y) for y in books["Year"].dropna().unique()}, reverse=True)
        payload["by_year"] = {
            str(y): _tier_payload(books, category_components, snum_map, y)
            for y in present
        }
    return payload


def _tier_payload(books, category_components, snum_map, year=None):
    """One tier-banded cohort. Extracted from the endpoint so `years=all` can build
    several without re-reading the engine or re-deriving the series map."""
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
        raise _server_error(e)
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
        raise _server_error(e)
    out = buf.getvalue().strip()
    if not ok:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not delete recommendation.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.get("/health")
def health(db: int = 0):
    """Liveness. `?db=1` additionally measures the round trip to Postgres.

    The DB probe is OPT-IN and must stay that way: this is the platform's health
    check endpoint, so making it depend on the database would turn a transient
    Postgres blip into a failed health check and a container restart — trading a
    slow request for an outage.

    `?db=1` exists because backend latency is dominated by how far the database is
    and how many round trips a request makes, and neither is measurable from
    outside the deployment. `connect_ms` is a pool borrow (near zero when the pool
    is warm and the connection was verified recently); `query_ms` is one SELECT 1,
    i.e. one round trip to Postgres. Read-only and cheap."""
    out = {"ok": True}
    if db:
        try:
            t0 = time.perf_counter()
            con = db_backend.connect(db_write.DB)
            t1 = time.perf_counter()
            con.execute("SELECT 1").fetchone()
            t2 = time.perf_counter()
            con.close()
            # The same query on a READ-ONLY connection. Reported alongside the
            # transactional one because the difference IS the cost of the implicit
            # BEGIN a pooled connection needs after its return rollback — roughly
            # 2 of every 3 round trips. Without both numbers this probe measures a
            # path most reads no longer take (see db_backend.readonly()).
            t3 = time.perf_counter()
            rcon = db_backend.connect(db_write.DB, readonly=True)
            rcon.execute("SELECT 1").fetchone()
            t4 = time.perf_counter()
            rcon.close()
            out["connect_ms"] = round((t1 - t0) * 1000, 1)
            out["query_ms"] = round((t2 - t1) * 1000, 1)
            out["query_readonly_ms"] = round((t4 - t3) * 1000, 1)
            out["backend"] = db_backend.backend()
            out["pool"] = {"min": getattr(db_backend, "DB_POOL_MIN", None),
                           "max": db_backend.DB_POOL_MAX,
                           "health_ttl_s": getattr(db_backend, "HEALTH_TTL_S", None),
                           # Which pgbouncer mode query traffic is on. 'session'
                           # here means the split silently fell back — the query
                           # pool is then sharing the 15-client cap with the
                           # listeners again. See db_backend's two-pooler note.
                           "query_pooler": db_backend.query_pooler_mode()}
        except Exception as e:
            out["db_error"] = type(e).__name__
    return out


def _resolve_version() -> dict:
    """Build identity of the running backend, resolved ONCE at process start.

    Source order: Railway injects the deployed commit at build time
    (RAILWAY_GIT_COMMIT_SHA / RAILWAY_GIT_BRANCH) — the authoritative hosted
    value; local dev falls back to reading git HEAD; else 'unknown'. Cheap and
    side-effect-free at import (one git call only when the env var is absent)."""
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
    branch = os.environ.get("RAILWAY_GIT_BRANCH", "").strip()
    source = "railway"
    if not sha:
        source = "git"
        try:
            import subprocess
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                stderr=subprocess.DEVNULL, timeout=5).decode().strip()
            if not branch:
                branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=PROJECT_ROOT,
                    stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        except Exception:
            sha, source = "", "unknown"
    return {
        "commit": sha or "unknown",
        "short": sha[:7] if sha else "unknown",
        "branch": branch or "unknown",
        "source": source,
        # Worker count AS THE PROCESS SEES IT. Without this the deployed worker
        # count is only inferrable from the database side (counting cache_sync
        # listener sessions), and that count is misleading behind the Supabase
        # pooler — its server backends outlive a redeploy, so they report the
        # POOLER's age and population rather than the app's. This is the number
        # that actually matters: main.py and db_backend divide the per-process
        # budgets by it, so if it ever disagrees with the Procfile's
        # LEDGER_WORKERS, the Procfile is not the thing starting the server.
        "workers": WORKERS,
        "workers_pinned": bool(os.environ.get("LEDGER_WORKERS")
                               or os.environ.get("WEB_CONCURRENCY")),
    }


_VERSION = _resolve_version()


@app.get("/api/version")
def version():
    """Deployed backend build identity — the git commit SHA the running process
    was built from, so a deploy is verifiable with one curl:
    `GET /api/version` → {"short": "<sha7>", "commit": ..., "branch": ..., "source": ...}.

    PUBLIC/unauthenticated by design: a build id is less revealing than the
    already-public /openapi.json, and deploy verification must work without a
    token. Resolved once at process start (see _resolve_version), so this is a
    constant-time dict return — no git call per request. NOT snapshotted to the
    static showcase (it's a live-backend deploy marker, meaningless there)."""
    return _VERSION


class SignupRequest(BaseModel):
    email: str
    password: str
    invite_code: str


@app.post("/api/signup")
def signup(req: SignupRequest, request: Request):
    """Invite-code-gated account creation (hosted multi-user). PUBLIC/global —
    the caller isn't authenticated yet, so no Depends(get_current_user_id); the
    invite code is the gate and it (plus the service-role key) lives only on the
    server (see signup.py). 404 when sign-up isn't configured (local/static)."""
    if not signup_mod.SIGNUP_ENABLED:
        raise HTTPException(status_code=404, detail="Sign-up is not enabled here.")
    # Rate-limit before the invite-code check so the code can't be brute-forced.
    _rate_limit(request, "signup", **_RL_SIGNUP)
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
    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e)
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

    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e, "LLM call failed")

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
            raise _server_error(e, "Queue update failed")

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
def get_read_queue(blurbs: bool = True,
                   user_id: str = Depends(auth.get_current_user_id),
                   user_md: dict = Depends(auth.get_current_user_metadata)):
    """Return all not-done recommendations with flat component scores and predicted rank.

    `blurbs=0` OMITS the `blurb` field from every row. Blurbs are a paragraph each and
    only render inside an expanded card, but they are ~40% of this response on a large
    TBR (221 KB of 262 KB of prose here) — so the read-queue page asks for the list
    without them and fetches one on expand (GET /api/recommendations/{title}/blurb).
    `keywords` is NOT droppable: the page's keyword filter searches it client-side over
    the whole list. Default True, so every other caller — the static export, the
    public-profile delegation — keeps the response shape it already had."""
    books, gw, gcw = _get_engine(user_id)[:3]
    rated_wa = books["WA"].values
    # Same-author analog counts drive the conformal interval bucket (author is the
    # engine's innermost density tier). Precompute once so the per-rec lookup is O(1).
    # Same-genre counts gate the favorite-genre cold-start prior the same way.
    author_counts = books["Author"].value_counts()
    genre_counts = books["Genre"].value_counts()
    cold_term = _get_cold_term(user_id, user_md.get("word_count_pref"),
                               user_md.get("fav_authors"),
                               user_md.get("fav_genres"),
                               user_md.get(STAR_GENRE_OFFSETS_KEY))

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = db_backend.connect(db_write.DB, readonly=True)   # SELECT only
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
        n_genre = int(genre_counts.get(genre_str, 0))
        wa = _cold_adjust_rec_wa(wa, words, series_number, author, genre_str,
                                 n_author, n_genre, cold_term)
        predicted_rank = int((rated_wa > wa).sum() + 1)

        rec = {
            "title": (title or "").strip(),
            "author": (author or "").strip(),
            "genre": genre_str,
            "series": (series or "").strip().strip("'\""),
            "series_number": _norm_snum(series_number),
            "words": words,
            "keywords": keywords or "",
            "components": components,
            "wa": round(wa, 4),
            "predicted_rank": predicted_rank,
            "category_avgs": category_avgs,
        }
        if blurbs:
            rec["blurb"] = blurb or ""
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
        raise _server_error(e, "Engine build failed")
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data
    g_info = ginfo.get(genre, {})
    if genre not in {row for row in books_e["Genre"].unique()}:
        raise HTTPException(status_code=422, detail=f"Genre '{genre}' not recognised.")
    try:
        p = pe.predict(title, author, genre, data)
    except Exception as e:
        raise _server_error(e, "Prediction failed")
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
    title: _CleanText
    author: _CleanText
    genre: Optional[str] = None   # None → auto-detect from the LLM
    grounded: bool = False        # False → fast memory scores; True → hybrid
                                  # (web-grounded) upgrade. Default is fast so the
                                  # candidate list scores instantly; the client
                                  # re-requests grounded=True to refine per book.
    force: bool = False           # True → skip every research-cache layer and
                                  # re-research this one book, overwriting its
                                  # cached entry (explicit refresh, never a purge).


def _genre_has_weights(genre, allowed_genres) -> bool:
    """Is this genre one the WA roll-up actually has weights for?

    Load-bearing because of how the roll-up degrades. Every WA computation reads
    `gw.get(genre, {}).get(cat, 0) or 0`, so a genre with no `genre_weights` row
    does not raise — it contributes ZERO from every category and the book comes
    back with a confident-looking full component breakdown and a WA of 0.00. That
    is the worst kind of wrong: it looks like an answer.

    Every other way a genre enters the engine is already guarded — the LLM
    detection path checks it (research_predict), candidate generation constrains
    each candidate to the list, the single-book injection drops an off-list genre,
    and db_write.add_book refuses one outright. A genre supplied directly by the
    CALLER was the one unchecked door.
    """
    return genre in set(allowed_genres)


def _build_research_response(user_id, title, author, eff_genre, genre_auto_detected,
                             scores, conf, blurb, keywords, words, from_cache,
                             sourcing, hybrid_available, engine_data, cache, user_md):
    """Shared assembly for a grounded-research prediction: correction → WA roll-up →
    display components → conformal 80% interval. Called by BOTH the authenticated
    /api/predict/research endpoint and the public /api/demo/predict endpoint, so the
    two can never drift. Pure computation over the read-only engine — no LLM call, no
    writes; the caller has already obtained `scores` (from the cache or a live
    research call) and resolved `eff_genre`. `user_md` may be {} (the demo has no
    per-user preferences), which just leaves the cold-start term off."""
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = engine_data
    # The reader's own prose→number scale, applied to the RAW research vector
    # before anything else (score_anchors). The research prompt and its cache stay
    # canonical for everyone; this re-prices what the reviews said onto this
    # reader's anchors, and is the exact identity map for anyone on the defaults.
    # The correction ladder below still trains on canonical raw scores — see the
    # module docstring for why both sides must not be remapped.
    scores = sa.remap_for_user(scores, user_id)
    try:
        corr_pool = _correction_pool(user_id, books_e)   # borrow the seed's calibration if new
        pairs, corr_models = _corr_statics(user_id, corr_pool)   # per-run statics, cached
        res = _rp.correct_and_predict(
            title, author, eff_genre, scores, conf, resid_sd,
            corr_pool, gw_e, gcw_e, cache, blurb=blurb, keywords=keywords,
            corr_models=corr_models, words=words, pairs=pairs,
            cold_term=_get_cold_term(user_id, user_md.get("word_count_pref"),
                                     user_md.get("fav_authors"),
                                     user_md.get("fav_genres"),
                                     user_md.get(STAR_GENRE_OFFSETS_KEY)),
            # Rank / total / grounding counts scope to the tenant's OWN library
            # (books_e), never the seed-borrowed correction pool (corr_pool). The
            # correction VALUE still borrows the seed; only the display denominator
            # changes — so a cold-start reader no longer sees "rank #2 of <seed>".
            rank_pool=books_e,
        )
    except Exception as e:
        raise _server_error(e, "Correction failed")

    # Category averages from corrected components (for display)
    cat_comps = books_e.attrs["category_components"]
    components_by_cat: dict = {}
    for cat, comps in cat_comps.items():
        components_by_cat[cat] = {c: _clean(round(res["scores"].get(c, 0), 2)) for c in comps}

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
        "genre_auto_detected": genre_auto_detected,
        "sourcing": sourcing,
        "hybrid_available": hybrid_available,
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


@app.post("/api/predict/research")
def predict_research(req: ResearchRequest, request: Request,
                     user_id: str = Depends(auth.get_current_user_id),
                     user_md: dict = Depends(auth.get_current_user_metadata)):
    """
    Grounded research prediction: research_rich_plus → correlation-smooth →
    author+genre correct → WA roll-up. One LLM API call (or cache hit).
    Returns corrected components, WA, CI, rank, grounding signals.
    """
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")

    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail="The prediction service is temporarily unavailable.")

    try:
        data = _get_engine(user_id)   # unpacked inside _build_research_response
    except Exception as e:
        raise _server_error(e, "Engine build failed")

    con = db_backend.connect(db_write.DB, readonly=True)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()

    # BEFORE the research call, not after: an unscoreable genre is knowable from
    # the request alone, and rejecting it afterwards would still have spent the
    # Anthropic call to produce a result that could only ever roll up to 0.00.
    if req.genre is not None and not _genre_has_weights(req.genre, allowed_genres):
        raise HTTPException(
            status_code=422,
            detail=f"'{req.genre}' isn't one of your genres, so it has no weights "
                   f"to score against. Pick one of: {', '.join(allowed_genres)}.")

    cache = _rp.load_cache()
    try:
        scores, conf, blurb, keywords, det_genre, words, from_cache = _rp.research_book(
            req.title, req.author, req.genre, client, cache,
            allowed_genres=allowed_genres, force=req.force,
        )
        _rp.save_cache(cache)
    except Exception as e:
        raise _server_error(e, "Research failed")

    eff_genre = req.genre or det_genre
    if eff_genre is None:
        raise HTTPException(status_code=422,
                            detail="Could not auto-detect a genre — pick one manually.")
    # Belt-and-braces on the DETECTED genre. research_predict already constrains
    # detection to this list, so this should be unreachable — but the roll-up
    # fails silently rather than loudly, and that is exactly the class of bug
    # worth paying one set lookup to make impossible.
    if not _genre_has_weights(eff_genre, allowed_genres):
        raise HTTPException(
            status_code=422,
            detail=f"'{eff_genre}' isn't one of your genres, so it has no weights "
                   f"to score against. Pick one of: {', '.join(allowed_genres)}.")

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

    # NOTE: the rich house-style blurb and the series/ordinal lookup are NOT done
    # here — they each cost an extra LLM call, and scoring many discover candidates
    # would multiply that. Both are deferred to /api/recommendations (save time),
    # so they're only paid for books the reader actually keeps. The plain research
    # blurb carried through below is what's shown while browsing; save upgrades it.
    return _build_research_response(
        user_id, req.title, req.author, eff_genre, req.genre is None,
        scores, conf, blurb, keywords, words, from_cache,
        sourcing="hybrid" if applied_grounded else "memory",
        hybrid_available=bool(grounding_on and not applied_grounded),
        engine_data=data, cache=cache, user_md=user_md)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC "TRY IT" DEMO — the flagship prediction, demoable WITHOUT an account.
# ─────────────────────────────────────────────────────────────────────────────
# This is the ONE unauthenticated write-nothing prediction surface (see the /try
# page). It is deliberately narrow and safe:
#   * Tenant-FIXED to SEED_USER_ID (the public showcase library, Michael) — it can
#     never read a private tenant, and takes no user_id from the caller.
#   * Read-only — it predicts; it never writes any tenant table. (A live cache-miss
#     persists the researched entry to the GLOBAL research_cache so the next visitor
#     is free, exactly as the authed path does — that is the only side effect.)
#   * Cost-bounded — a CACHED book is engine-only (free); a live cache-miss spends
#     one Anthropic call and is gated by a tight per-IP hourly bucket AND a single
#     global daily cap. When the live budget is spent it returns available=False and
#     the client falls back to the always-free cached examples — it never errors out
#     or silently burns credits.
# It reuses _build_research_response, so its numbers are identical to the real app.

class DemoPredictRequest(BaseModel):
    title: _CleanText
    author: _CleanText = ""       # optional — improves the author-analog signal
    genre: Optional[str] = None   # None → auto-detect (cached genre or live)


def _demo_unavailable(message: str, req: "DemoPredictRequest") -> dict:
    """Graceful 200 the client renders as 'not in the demo set' (with example
    chips) — used for an uncached book once the live budget is spent, or when a
    genre can't be resolved. Never spends an Anthropic call."""
    return {"available": False, "message": message,
            "title": req.title, "author": req.author, "genre": req.genre}


def _seed_library_genre(title: str) -> Optional[str]:
    """Genre fallback for an OLDER cached entry that predates the stored `genre`
    field (~128 of the 620 seed entries are score-only). Look the title up in the
    SEED library — rated books, then TBR — so the demo auto-resolves a genre for a
    famous title instead of asking a visitor to pick one. Read-only, tenant-fixed
    to SEED_USER_ID, case-insensitive; None if not found. `tbl` is a fixed literal,
    never user input (same pattern as _book_count)."""
    con = db_backend.connect(db_write.DB, readonly=True)
    try:
        for tbl in ("books", "recommendations"):
            r = con.execute(
                f"SELECT genre FROM {tbl} WHERE lower(title)=lower(?) AND user_id=? "
                f"AND genre IS NOT NULL LIMIT 1", (title, SEED_USER_ID)).fetchone()
            if r and r[0]:
                return r[0]
    finally:
        con.close()
    return None


@app.post("/api/demo/predict")
def demo_predict(req: DemoPredictRequest, request: Request):
    """PUBLIC, UNAUTHENTICATED flagship-prediction demo. See the section header
    above for the safety envelope (tenant-fixed to the showcase library, read-only,
    cost-capped)."""
    _rate_limit(request, "demo", **_RL_DEMO, user_id=None)   # overall per-IP throttle
    if _rp is None:
        raise HTTPException(status_code=503, detail="Prediction service unavailable.")

    uid = SEED_USER_ID

    # A caller-supplied genre is the one input here that nothing downstream
    # checks (see _genre_has_weights). Validated ONLY when one is actually sent:
    # with req.genre None the genre comes from the research cache, the seed
    # library, or LLM detection — all already constrained — and this path stays
    # free of the extra round trip, which matters because a cache hit is the
    # demo's fast path.
    if req.genre is not None:
        con = db_backend.connect(db_write.DB, readonly=True)
        _allowed = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
        con.close()
        if not _genre_has_weights(req.genre, _allowed):
            return _demo_unavailable(
                f"'{req.genre}' isn't a genre this library scores against — "
                f"leave the genre blank and it will be detected automatically.", req)

    try:
        engine_data = _get_engine(uid)
    except Exception as e:
        raise _server_error(e, "Engine build failed")

    cache = _rp.load_cache()
    # Already analyzed? file cache → durable store. A hit is engine-only (free).
    entry = _rp.rl.cache_lookup(cache, req.title)
    if entry is None:
        entry = _rp.db_cache_get(_rp.CACHE, req.title)

    if entry is not None:
        # Older score-only entries carry no genre; recover it from the seed library
        # so a famous title still resolves without a manual pick.
        eff_genre = req.genre or entry.get("genre") or _seed_library_genre(req.title)
        if eff_genre is None:
            return _demo_unavailable(
                "Couldn't determine a genre for that title — try one of the examples.", req)
        resp = _build_research_response(
            uid, req.title, req.author, eff_genre, req.genre is None,
            entry["scores"], entry.get("conf", "?"),
            entry.get("blurb", ""), entry.get("keywords", ""),
            entry.get("words"), True,   # words as-stored; never estimate (no LLM)
            sourcing="memory", hybrid_available=False,
            engine_data=engine_data, cache=cache, user_md={})
        resp["available"] = True
        return resp

    # Cache MISS → a live Anthropic call. Gate on the per-IP hourly bucket AND the
    # single global daily cap; if either is spent, fall back gracefully (no call).
    if not _rate_limit(request, "demo_live", **_RL_DEMO_LIVE, user_id=None,
                       raise_on_limit=False):
        return _demo_unavailable(
            "You've reached the live-prediction limit for now — the example books "
            "are instant, or try again a little later.", req)
    if not _rate_limit(request, "demo_live_global", max_calls=_DEMO_LIVE_DAILY_CAP,
                       window_s=_DEMO_LIVE_WINDOW_S, user_id=_DEMO_GLOBAL_PRINCIPAL,
                       raise_on_limit=False):
        return _demo_unavailable(
            "The live demo has hit today's cap for brand-new titles. The example "
            "books are instant — or check back tomorrow.", req)

    try:
        client = _rp.get_client()
    except Exception:
        return _demo_unavailable(
            "Live prediction is temporarily unavailable — try an example book.", req)

    con = db_backend.connect(db_write.DB, readonly=True)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    con.close()
    try:
        scores, conf, blurb, keywords, det_genre, words, from_cache = _rp.research_book(
            req.title, req.author, req.genre, client, cache,
            allowed_genres=allowed_genres, force=False,
        )
        _rp.save_cache(cache)   # persist for the next visitor (global cache; read-only re: tenants)
    except Exception as e:
        raise _server_error(e, "Research failed")

    eff_genre = req.genre or det_genre
    if eff_genre is None:
        return _demo_unavailable(
            "Couldn't determine a genre for that title — try one of the examples.", req)
    resp = _build_research_response(
        uid, req.title, req.author, eff_genre, req.genre is None,
        scores, conf, blurb, keywords, words, from_cache,
        sourcing="memory", hybrid_available=False,
        engine_data=engine_data, cache=cache, user_md={})
    resp["available"] = True
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVER — generate candidates then score them individually
# ─────────────────────────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    request: _CleanQuery
    # Optional upper bound. When omitted, the LLM infers the count from the
    # request wording (e.g. "the 5 main books of X" → 5).
    max_candidates: Optional[int] = None


@app.post("/api/discover/candidates")
def discover_candidates(req: DiscoverRequest, request: Request,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Generate candidate book titles for a free-text request (1 API call)."""
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")

    books = _get_engine(user_id)[0]
    cache = _rp.load_cache()

    con = db_backend.connect(db_write.DB, readonly=True)
    allowed_genres = sorted(r[0] for r in con.execute("SELECT genre FROM genre_weights"))
    tbr_books = [(t or "", a or "") for t, a in con.execute(
        "SELECT title, author FROM recommendations WHERE user_id=?", (user_id,))]
    con.close()

    read_books = list(zip(books["Book"].tolist(), books["Author"].tolist()))
    # Rated library WITH genre — lets a named single book be fuzzy-resolved to its
    # canonical title/author/genre (spelling-variance fallback + metadata).
    library = list(zip(books["Book"].tolist(), books["Author"].tolist(),
                       books["Genre"].tolist()))

    try:
        result = _rp.generate_candidates(
            req.request.strip(), allowed_genres, read_books,
            tbr_books=tbr_books, n=req.max_candidates, client=client,
            library=library,
        )
    except Exception as e:
        raise _server_error(e, "Candidate generation failed")

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
    title: _CleanText
    author: _CleanText
    genre: str


@app.get("/api/recommendations/{title}/blurb")
def get_recommendation_blurb(title: str,
                             user_id: str = Depends(auth.get_current_user_id)):
    """One recommendation's stored blurb — the lazy half of `/api/read-queue?blurbs=0`.

    Read-only and tenant-scoped, so it can never surface another reader's prose. Returns
    an empty string (not 404) when the row exists but has no blurb yet, which is exactly
    what the card needs to decide between rendering a blurb and offering to generate one.
    404 only when the reader has no such active recommendation. Unthrottled, like the
    other tenant-scoped data GETs — it is one indexed row read, auth-gated, and only
    reachable for the caller's own recommendations."""
    con = db_backend.connect(db_write.DB, readonly=True)
    row = con.execute(
        "SELECT blurb FROM recommendations "
        "WHERE LOWER(title)=LOWER(?) AND done=0 AND user_id=? ORDER BY id DESC LIMIT 1",
        (title.strip(), user_id)).fetchone()
    con.close()
    if row is None:
        raise HTTPException(status_code=404, detail="No such recommendation.")
    return {"blurb": row[0] or ""}


@app.post("/api/recommendations/generate-meta")
def generate_recommendation_meta(req: GenerateMetaRequest, request: Request,
                                 user_id: str = Depends(auth.get_current_user_id)):
    """Generate blurb + keywords for a recommendation that was added without research."""
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _rp is None:
        raise HTTPException(status_code=500, detail="research_predict not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail="The prediction service is temporarily unavailable.")
    try:
        blurb, keywords = _rp.generate_blurb_keywords(req.title, req.author, req.genre, client)
    except Exception as e:
        raise _server_error(e, "Generation failed")
    if not blurb and not keywords:
        raise HTTPException(status_code=422,
                            detail="Model returned nothing usable for this book — try again.")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            db_write.set_recommendation_meta(req.title, blurb or None, keywords or None,
                                             user_id=user_id)
    except Exception as e:
        raise _server_error(e)
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
    genre_counts = books["Genre"].value_counts()
    cold_term = _get_cold_term(user_id, user_md.get("word_count_pref"),
                               user_md.get("fav_authors"),
                               user_md.get("fav_genres"),
                               user_md.get(STAR_GENRE_OFFSETS_KEY))

    COMPONENTS = db_write.FICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)

    con = db_backend.connect(db_write.DB, readonly=True)

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
        n_genre = int(genre_counts.get(genre_str, 0))
        wa = _cold_adjust_rec_wa(wa, words, series_number, author, genre_str,
                                 n_author, n_genre, cold_term)
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
        raise _server_error(e)
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not set year.")
    _invalidate_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


# ─────────────────────────────────────────────────────────────────────────────
# SERIES
# ─────────────────────────────────────────────────────────────────────────────

def _series_terms(row):
    """The per-term breakdown of one series' score, so the UI can show WHY a
    series ranks where it does instead of just asserting a number. Avg WA plus
    these four reconstructs Adjusted WA exactly (up to the shared clamp)."""
    weakest = row["Weakest Pct"]
    return {
        "consistency": _clean(round(float(row["Consistency"]), 3)),
        "peak": _clean(round(float(row["Peak"]), 3)),
        "finale": _clean(round(float(row["Finale"]), 3)),
        # Standing charge for a series that has not ended. Distinct from finale=0,
        # which only means "no evidence about the ending".
        "unfinished": _clean(round(float(row["Unfinished"]), 3)),
        "evidence": _clean(round(float(row["Evidence"]), 3)),
        # Share of the reader's rated books the series' WEAKEST volume beats.
        # None for a one-book series, where there is nothing to be consistent
        # about — the UI must show that as "n/a", never as 0.
        "weakest_pct": (None if weakest is None or pd.isna(weakest)
                        else _clean(round(float(weakest), 4))),
        "peak_lift": _clean(round(float(row["Peak Lift"]), 3)),
        "finale_lift": _clean(round(float(row["Finale Lift"]), 3)),
        "complete": bool(row["Complete"]),
    }


@app.get("/api/series")
def get_series(user_id: str = Depends(auth.get_current_user_id)):
    """Series rankings: per-series aggregates sorted by the series score."""
    books = _get_engine(user_id)[0]
    sa = views_mod.series_aggregate(
        books, series_meta=db_write.get_series_meta(user_id=user_id))
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
            **_series_terms(row),
        })
    return {"series": result}


@app.get("/api/series/tiers")
def get_series_tiers(user_id: str = Depends(auth.get_current_user_id)):
    """Series tier list: same bands as book tier list but by the series score
    (S+ >= 9.0)."""
    books = _get_engine(user_id)[0]
    sa = views_mod.series_aggregate(
        books, series_meta=db_write.get_series_meta(user_id=user_id))
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
            **_series_terms(row),
        })
    counts = views_mod.tier_counts(tiered)
    return {"series": result, "tier_order": views_mod.TIER_ORDER, "tier_counts": counts}


class SeriesCompleteRequest(BaseModel):
    complete: bool


@app.put("/api/series/{series_name}/complete")
def put_series_complete(series_name: str, req: SeriesCompleteRequest,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Mark a series finished (or ongoing again). This is what licenses the
    Finale term: the last volume's Ending only counts as an ENDING once the
    series has actually ended, so an ongoing series is never penalised for a
    finale it hasn't written yet. No engine invalidation — the flag is read
    per-request in the series routes, not baked into the cached engine."""
    if not db_write.set_series_complete(series_name, req.complete,
                                        user_id=user_id):
        raise HTTPException(
            status_code=404,
            detail=f"No rated books found in a series called '{series_name}'.")
    return {"ok": True, "series": series_name, "complete": bool(req.complete)}


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

    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e)
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
        raise _server_error(e)
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
    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e)
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
        raise _server_error(e)
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not set year.")
    _invalidate_nf_engine(user_id)
    return {"ok": True, "message": out.replace("✓", "").strip()}


class NonfictionResearchRequest(BaseModel):
    title: _CleanText
    author: _CleanText
    genre: Optional[str] = None
    # Explicit no-cache refresh (parity with the fiction ResearchRequest.force):
    # skip both cache layers, re-research, and overwrite this one entry.
    force: bool = False


@app.post("/api/nonfiction/predict/research")
def predict_nf_research(req: NonfictionResearchRequest, request: Request,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Grounded nonfiction prediction: one LLM call (or cache hit) scores the 12
    components, then they roll up through the SAME nonfiction math (category
    averages, Quality-lean WA, Total Average) and are ranked by Total Average
    against the rated nonfiction books. Always low-confidence at n=6.

    Returns the fiction-shaped superset the shared Predict UI consumes — grounding
    counts, from_cache, and the (empty) blurb/series fields fiction carries. There
    is NO served interval at all: nonfiction has no residual table, and the
    regression guard forbids a variance-derived substitute (CLAUDE.md)."""
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _nr is None:
        raise HTTPException(status_code=500, detail="nonfiction_research not available")
    eff_genre = req.genre or "Nonfiction"
    cache = _rp.load_cache(_nr.NF_CACHE)
    try:
        data = _get_nf_engine(user_id)
        r = _nr.research_and_predict(req.title, req.author, eff_genre,
                                     data=data, cache=cache, force=req.force,
                                     # The reader's own prose→number scale, applied
                                     # to the raw vector before the roll-up (the
                                     # fiction path does the same in
                                     # _build_research_response).
                                     anchors=sa.load_anchors(user_id))
        _rp.save_cache(cache, _nr.NF_CACHE)
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise _server_error(e, "Research failed")
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
        # Fiction-shaped parity fields (the shared card reads these). Nonfiction has
        # no web-grounding path and no residual table, so sourcing is always memory,
        # no hybrid upgrade is offered, and no interval is attached.
        "n_genre": r["n_genre"], "n_author": r["n_author"],
        "from_cache": r["from_cache"], "words": None,
        "series": "", "series_number": None,
        "blurb": "", "keywords": "",
        "sourcing": "memory", "hybrid_available": False,
        "genre_auto_detected": False,
    }


class NonfictionDiscoverRequest(BaseModel):
    request: _CleanQuery
    n: Optional[int] = None


@app.post("/api/nonfiction/discover/candidates")
def discover_nf_candidates(req: NonfictionDiscoverRequest, request: Request,
                           user_id: str = Depends(auth.get_current_user_id)):
    """Brainstorm nonfiction candidates for a free-text request (one cheap Sonnet
    call), excluding books already in your nonfiction library or TBR."""
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _nr is None:
        raise HTTPException(status_code=500, detail="nonfiction_research not available")
    try:
        client = _rp.get_client()
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="apikey.txt not found — add your Anthropic API key.")
    except Exception as e:
        raise HTTPException(status_code=503, detail="The prediction service is temporarily unavailable.")
    request = (req.request or "").strip()
    if not request:
        raise HTTPException(status_code=422, detail="Enter a request.")
    con = db_backend.connect(db_write.DB, readonly=True)
    have = {r[0].strip().lower() for r in con.execute(
        "SELECT title FROM nonfiction_books WHERE user_id=?", (user_id,)) if r[0]}
    have |= {r[0].strip().lower() for r in con.execute(
        "SELECT title FROM nonfiction_recommendations WHERE user_id=?", (user_id,)) if r[0]}
    con.close()
    try:
        # Avoidance moved INTO the generator so an explicit single-book request
        # can be guaranteed as a candidate even when it's already in library/TBR.
        cands = _nr.discover_nonfiction_candidates(
            request, n=req.n or 8, client=client, avoid_titles=have)
    except Exception as e:
        raise _server_error(e, "Candidate generation failed")
    # Flag which candidates are already researched (free to score), so the table
    # shows cached/new like fiction. File cache only (cheap, no per-candidate DB
    # round-trip) — the durable store is still consulted at score time, matching
    # the fiction discover flow.
    nf_cache = _rp.load_cache(_nr.NF_CACHE)
    for c in cands:
        c["cached"] = _rl.cache_lookup(nf_cache, c.get("title", "")) is not None
    note = "" if cands else "Every suggestion is already in your library or TBR — try a different request."
    return {"candidates": cands, "request": request, "note": note, "sources": []}


# ─── Nonfiction TBR (recommendations + read queue) ───────────────────────────

@app.get("/api/nonfiction/read-queue")
def get_nf_read_queue(user_id: str = Depends(auth.get_current_user_id)):
    """Not-done nonfiction recommendations with components, category averages,
    predicted WA (computed on read), predicted rank by WA, and a DIRECTIONAL
    prediction interval + upside (mirrors the fiction read-queue). The interval is a
    small-sample CONFORMAL band (empirical P80 of the nonfiction leave-one-out |WA
    error|), NOT a ±sd band, and is labeled directional — nonfiction has too few
    rated books to calibrate it. Omitted when the library is too small to fit one."""
    books, gw, gcw = _get_nf_engine(user_id)
    bt = nfe.add_total_average(books)
    rated_wa = bt["WA"].values

    # DIRECTIONAL served-interval half-width — one empirical-P80 width for the whole
    # library (no author-density buckets at n≈6). Best-effort: any failure just omits
    # the interval (a width is never invented). See nonfiction_walkforward.
    hw, hw_n = None, 0
    if _nfw is not None:
        try:
            hw, hw_n = _nfw.interval_half_width(data=(books, gw, gcw))
        except Exception:
            hw = None

    COMPONENTS = db_write.NONFICTION_COMPONENTS
    comp_cols = ", ".join(f'"{c}"' for c in COMPONENTS)
    con = db_backend.connect(db_write.DB, readonly=True)   # SELECT only
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
        rec = {
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
            # Rank by WA (matches nfe.rank_table, the WA-primary nonfiction ranking).
            "predicted_rank": int((rated_wa > wa).sum() + 1) if wa == wa else None,
        }
        # Directional interval + upside, centred on the predicted WA (0–10 clamped),
        # only when a half-width was fit and the WA is real.
        if hw is not None and wa == wa:
            rec["wa_low"] = round(max(0.0, wa - hw), 4)
            rec["wa_high"] = round(min(10.0, wa + hw), 4)
            rec["upside"] = round(min(10.0, wa + UPSIDE_FRAC * hw), 4)
            rec["interval_label"] = f"directional · n={hw_n}"
        result.append(rec)
    result.sort(key=lambda b: (b["wa"] is not None, b["wa"] or 0.0), reverse=True)
    return {"recommendations": result, "genres": []}


@app.post("/api/nonfiction/recommendations/{title}/repredict")
def repredict_nf_recommendation(title: str, request: Request,
                                user_id: str = Depends(auth.get_current_user_id)):
    """GRANULAR re-prediction for ONE unread nonfiction book.

    Deliberately NOT the same operation as its fiction sibling, because the two
    tracks predict differently. Fiction re-prediction is worth doing for free: its
    stored scores come from a correction trained on the reader's rated library, so
    they move as the library grows. Nonfiction has NO correction layer — its stored
    scores are the research vector under the reader's anchors, independent of the
    library — so the cached path would return an identical vector every time.

    This therefore FORCES a fresh research call (force=True), which is the only
    thing that genuinely re-predicts a nonfiction book. That means it always spends
    one Opus call: no cache-hit fast path exists here, unlike /api/predict/research.
    Owner decision, 2026-08-14. Same LLM rate-limit bucket accordingly.

    No delta_log row is written (the table is fiction-shaped) and no interval is
    returned (nonfiction has no residual table). 404 if the title is not on this
    reader's active nonfiction TBR."""
    _rate_limit(request, "llm", **_RL_LLM, user_id=user_id)
    if _repred is None or _nr is None or _rp is None:
        raise HTTPException(status_code=503,
                            detail="Re-prediction is unavailable on this deployment.")
    try:
        data = _get_nf_engine(user_id)
    except Exception as e:
        raise _server_error(e, "Nonfiction engine build failed")

    report = _repred.repredict_nonfiction_one(
        title, get_data=lambda: data, cache=_rp.load_cache(_nr.NF_CACHE),
        user_id=user_id, write_lock=_repred_lock)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"No unread nonfiction book titled '{title}' on your list.")
    if report.get("skipped"):
        raise HTTPException(
            status_code=422,
            detail=f"Could not re-predict this book ({report['skipped']}).")
    if report.get("changed") and not report.get("written"):
        raise HTTPException(status_code=500,
                            detail="Re-predicted, but the new scores could not be saved.")
    return {"ok": True, "report": report}


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
    """Save a researched nonfiction book to the TBR. Generates a blurb + keywords at
    save time when the client didn't supply them (deferred from scoring so the extra
    LLM call is only paid for books actually kept — mirrors the fiction save path).
    Best-effort: a failure falls back to empty and never blocks the save."""
    blurb = (req.blurb or "").strip()
    keywords = (req.keywords or "").strip()
    if (not blurb or not keywords) and _rp is not None:
        try:
            b, k = _rp.generate_blurb_keywords(
                req.title, req.author or "", req.genre or "Nonfiction", _rp.get_client())
            blurb = blurb or b
            keywords = keywords or k
        except Exception:
            pass  # keep whatever the request carried (possibly empty)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = db_write.add_nonfiction_recommendation(
                title=req.title, author=req.author, genre=req.genre,
                scores=req.scores, series=req.series,
                series_number=req.series_number, words=req.words,
                blurb=blurb or None, keywords=keywords or None, user_id=user_id)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _server_error(e)
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
        raise _server_error(e)
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
        raise _server_error(e)
    out = buf.getvalue().strip()
    if not ok:
        raise HTTPException(status_code=422, detail=out.replace("✗", "").strip() or "Could not update.")
    return {"ok": True, "message": out.replace("✓", "").strip()}


@app.get("/api/nonfiction/queue")
def get_nf_queue(user_id: str = Depends(auth.get_current_user_id)):
    """Ordered nonfiction read-queue titles."""
    con = db_backend.connect(db_write.DB, readonly=True)
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
        raise _server_error(e)
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
        raise _server_error(e)
    out = buf.getvalue().strip()
    if not ok:
        msg = out.replace("✗", "").strip()
        raise HTTPException(status_code=422, detail=msg or "Could not save recommendation.")

    # Ground the saved rec in the BACKGROUND (off this response): run the deferred
    # web_search server-side and upgrade its stored scores from memory to the
    # calibrated grounded prediction. Non-blocking + best-effort — the save returns
    # now; the upgrade lands async and shows on the next fetch. A no-op (cache hit,
    # already grounded) for a book the client already refined. Bounded width keeps
    # it polite to the rate limiter (see _ground_executor).
    _submit_background_ground(req.title, req.author, req.genre, user_id)
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


def _read_order_keys(user_id):
    """Chronological sort key per rated book title, for walk-forward validation.
    Prefers the explicit read_seq rank (the delta-log reading-order convention);
    falls back to (year_read, read_month, insertion id) with NULL year/month
    sorted after known values in their group. The engine frame's schema is fixed
    (no read_seq/read_month columns), so these are fetched here read-only and
    passed alongside it. Uniform 4-tuples so keys always compare cleanly."""
    con = db_backend.connect(db_write.DB, readonly=True)
    rows = con.execute(
        'SELECT title, year_read, read_month, read_seq, id FROM books '
        'WHERE user_id=?', (user_id,)).fetchall()
    con.close()
    keys = {}
    for title, year, month, seq, rid in rows:
        if seq is not None:
            keys[(title or "").strip()] = (0, int(seq), 0, 0)
        else:
            keys[(title or "").strip()] = (
                1,
                int(year) if year is not None else 9999,
                int(month) if month is not None else 13,
                int(rid),
            )
    return keys


@app.post("/api/calibration/walkforward")
def run_walkforward_validation(user_id: str = Depends(auth.get_current_user_id)):
    """
    Honest walk-forward validation: the caller's books are replayed in read
    order (read_seq when present, else year_read + read_month + insertion
    order) and each one is predicted by an engine fit ONLY on the books read
    before it — no future leakage, unlike leave-one-out. Refits the engine ~n
    times — SLOW (seconds). Triggered explicitly by the user on the Calibration
    page, not on every load. 422 when the library is under the burn-in size.
    """
    books, gw, gcw = _get_engine(user_id)[:3]
    try:
        result = ve.run_walkforward(books=books, gw=gw, gcw=gcw,
                                    order=_read_order_keys(user_id))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _server_error(e, "walk-forward validation")
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
        raise _server_error(e, "Could not read comparison")


@app.get("/api/track-record")
def get_track_record(user_id: str = Depends(auth.get_current_user_id)):
    """Per-user Track Record: predicted-vs-actual for the books THIS reader has
    finished, in reading order, plus the rolling-MAE curve, MAE by genre, and
    served-band coverage — all computed from their own delta_log.

    TENANT-SCOPED, READ-ONLY, zero-API. Fetches this user's delta_log rows,
    reduces them to one authoritative row per genuinely-finished book via
    delta_log_view.visible_rows (same helper /api/delta-log uses), enriches
    each row's missing mechanism-metadata from any other row for the same
    title (so the served-coverage/raw-baseline stats work on pre-metadata
    live rows), and hands the deduped rows to track_record.build_track_record.
    Returns 404 when the reader has fewer than track_record.MIN_TRACK_RECORD
    finished predictions — the frontend renders a "not enough yet" state.

    The engine-wide validation numbers (reference-library walk-forward MAE,
    served-band coverage on the harness) moved to /api/engine-validation and
    feed the Methodology page; the two payloads are decoupled by design so a
    change to one can't silently redefine the other."""
    con = db_backend.connect(db_write.DB, readonly=True)
    try:
        # Match /api/delta-log's read shape, plus the mechanism columns the
        # per-user builder consumes (pred_genre/pred_author/corr_wa/n_author).
        rows = con.execute(
            "SELECT id, title, pred_wa, act_wa, pred_genre, pred_author, "
            "corr_wa, n_author, n_genre, pred_words, tag, logged_at, user_id "
            "FROM delta_log WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        cols = ["id", "title", "pred_wa", "act_wa", "pred_genre", "pred_author",
                "corr_wa", "n_author", "n_genre", "pred_words", "tag",
                "logged_at", "user_id"]
        entries = [dict(zip(cols, r)) for r in rows]

        # Finished-books set + reading order (same encoding /api/delta-log uses
        # at the query below), plus the genre/author/series/year fallback the
        # builder uses when a delta_log row lacks pred_genre / pred_author.
        finished, read_order, book_meta = set(), {}, {}
        for (t, g, a, s, sn, yr, mo, seq) in con.execute(
            "SELECT title, genre, author, series, series_number, year_read, "
            "read_month, read_seq FROM books WHERE user_id=? AND status=?",
            (user_id, "finished"),
        ).fetchall():
            key = (t or "").strip().lower()
            finished.add(key)
            book_meta[key] = {
                "genre": g, "author": a, "series": s, "series_number": sn,
                "year_read": yr,
            }
            if yr is not None:
                read_order[key] = (int(yr) * 100 + (int(mo) if mo else 0)) * 1_000_000 \
                    + (int(seq) if seq else 0)
    finally:
        con.close()

    visible = delta_log_view.visible_rows(
        entries, finished, db_write.DELTA_BACKFILL_MARKER, read_order=read_order)
    visible = tr.enrich_missing_meta(visible, entries)

    # Attach each finished book's CURRENT WA to the meta the builder consumes.
    # The engine's books DataFrame is recomputed live from the reader's current
    # component ratings + weights, so this is what THEIR score is right now —
    # not the delta_log's frozen act_wa (which drifts when they edit ratings
    # after finishing the book). The builder prefers current_wa as "actual"
    # and only falls back to act_wa when current_wa is missing.
    try:
        eng_books, _gw, _gcw, _c, _r2, _rsd, _gi, _up = _get_engine(user_id)
        for _, row in eng_books.iterrows():
            k = str(row.get("Book") or "").strip().lower()
            if not k:
                continue
            try:
                wa = float(row["WA"])
            except (TypeError, ValueError, KeyError):
                continue
            book_meta.setdefault(k, {})["current_wa"] = wa
    except Exception:
        # Never let an engine-build failure hide the Track Record; the builder
        # transparently falls back to delta_log's frozen act_wa if current_wa
        # is absent, so the page still renders (with slight staleness on
        # books whose ratings were edited after finishing).
        pass

    payload = tr.build_track_record(
        visible, read_order, residuals=_RESIDUALS, book_meta=book_meta,
    )
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Track Record needs at least {tr.MIN_TRACK_RECORD} "
                    "finished predictions to show meaningful accuracy."),
        )
    return payload


@app.get("/api/engine-validation")
def get_engine_validation():
    """Engine-wide walk-forward validation (reference library). Global, not
    tenant-scoped — this is the ENGINE's honest chronological accuracy,
    consumed by the Methodology page. Reads the committed validation/
    artifacts through engine_validation.build_engine_validation and computes
    served-band coverage via the canonical intervals module. Returns 404 when
    the artifacts haven't been generated yet (allow_404 in the export)."""
    payload = ev.build_engine_validation()
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
        raise _server_error(e, "Engine build failed")
    # Calibration provenance is a CONTINUUM now (smooth cold-start shrinkage), so
    # the flag reports three states and carries the actual own-fit weight: the seed
    # (and any tenant whose ramp has effectively converged) is "own", a tenant below
    # OWN_FIT_FLOOR rides the prior whole ("borrowed_seed"), everyone between is
    # "blended". blend_weight is the fraction of their OWN fit.
    if _uid(user_id) == SEED_USER_ID:
        blend_weight = 1.0
    else:
        blend_weight = _own_fit_weight(len(books))
    model_source = ("own" if blend_weight >= 0.995
                    else "borrowed_seed" if blend_weight <= 0.0
                    else "blended")
    return ep.build_engine_parameters(
        books, gw, gcw, r2, resid_sd, residuals=_RESIDUALS,
        cold_term=_get_cold_term(user_id, user_md.get("word_count_pref"),
                                 user_md.get("fav_authors"),
                                 user_md.get("fav_genres"),
                                 user_md.get(STAR_GENRE_OFFSETS_KEY)),
        model_source=model_source,
        min_own_fit=MIN_OWN_FIT,
        blend_weight=round(float(blend_weight), 4),
        anchors=sa.load_anchors(user_id),
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
    con = db_backend.connect(db_write.DB, readonly=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC PROFILES — opt-in cross-user browse (rankings / queue / tiers / stats)
# ─────────────────────────────────────────────────────────────────────────────
# A deliberate, gated cross-tenant read path that coexists with the tenant
# isolation enforced everywhere else. Every route here is AUTH-gated (the VIEWER
# must be signed in — cross-user browse is a hosted-app feature, off the static
# showcase). Identity has TWO parts on these routes: `viewer_id` from the verified
# token (rate-limit principal + must-be-signed-in) and `target_uid` resolved from
# the {handle}, gated by is_public. The intentional cross-tenant hole is EXACTLY
# `_resolve_public_target` → target_uid: everything downstream reuses the existing
# tenant-scoped handlers UNCHANGED, so a viewer sees the owner's own rankings
# computed on the owner's OWN weights (never re-scored on the viewer's). No
# prediction math is reimplemented; these are thin delegations.

class ProfilePayload(BaseModel):
    handle: str
    display_name: Optional[str] = None
    is_public: bool = False


def _book_count(table: str, user_id: str) -> int:
    """Cheap COUNT(*) for a tenant's rated library — used by the directory so it
    doesn't have to warm every public tenant's full engine just to show a count.
    `table` is a fixed literal ('books' / 'nonfiction_books'), never user input."""
    con = db_backend.connect(db_write.DB, readonly=True)
    try:
        r = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (user_id,)).fetchone()
    finally:
        con.close()
    return int(r[0]) if r and r[0] is not None else 0


def _resolve_public_target(handle: str) -> str:
    """Resolve a PUBLIC profile handle → its user_id, or raise 404. Returns 404
    (never 403) for a missing OR private handle, so a private handle's existence is
    never confirmed. This is the one intentional cross-tenant read; the caller has
    already been authenticated + rate-limited as the viewer."""
    prof = db_write.get_profile_by_handle(handle)
    if prof is None or not prof["is_public"]:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return prof["user_id"]


def _cross_user_target(handle: str, request: Request, viewer_id: str) -> str:
    """Rate-limit the VIEWER, then resolve the public target. Shared preamble for
    every cross-user data route."""
    _rate_limit(request, "profile", **_RL_PROFILE, user_id=viewer_id)
    return _resolve_public_target(handle)


# ── The viewer's OWN profile (settings: claim a handle, toggle public) ──
@app.get("/api/profile/me")
def get_my_profile(user_id: str = Depends(auth.get_current_user_id)):
    """The caller's own profile ({handle, display_name, is_public, …}) or null if
    they haven't claimed one yet."""
    return db_write.get_profile_by_user(user_id)


@app.put("/api/profile/me")
def set_my_profile(payload: ProfilePayload,
                   user_id: str = Depends(auth.get_current_user_id)):
    """Claim/update the caller's public profile. Validates the handle + enforces
    global uniqueness in db_write; a bad/taken handle → 400."""
    try:
        return db_write.set_profile(
            user_id, payload.handle,
            display_name=payload.display_name, is_public=payload.is_public)
    except db_write.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ───────────────────────────── Goodreads import ─────────────────────────────
# Onboarding accelerator: a signed-in user uploads their Goodreads "Export
# Library" CSV; we parse it (goodreads_import — pure, no DB) and stage the
# metadata per-user (db_write import_staging). No component scores are created
# here. Read-shelf books become a ranking backlog the user works through;
# to-read books drain to recommendations at commit (a later phase). Every route
# is auth-gated on the caller + keyed to its own rate-limit bucket; all staging
# reads/writes are tenant-scoped by user_id in db_write.
_MAX_CSV_CHARS = 8_000_000  # ~8 MB of text — a very large library export


class GoodreadsImportRequest(BaseModel):
    csv_text: str
    filename: Optional[str] = None


class ImportCommitRequest(BaseModel):
    batch_id: Optional[str] = None
    ids: Optional[list[str]] = None


class StagingRowUpdate(BaseModel):
    """A review edit. Only the fields the client actually sends are applied
    (model_dump(exclude_unset=True)); an explicit null clears a field."""
    kind: Optional[str] = None
    title: Optional[_CleanText] = None
    author: Optional[_CleanText] = None
    genre: Optional[_CleanText] = None
    series: Optional[_CleanText] = None
    series_number: Optional[int] = None
    words: Optional[int] = None
    year_read: Optional[int] = None
    read_month: Optional[int] = None
    state: Optional[str] = None


@app.post("/api/import/goodreads")
def import_goodreads(payload: GoodreadsImportRequest, background_tasks: BackgroundTasks,
                     request: Request, user_id: str = Depends(auth.get_current_user_id)):
    """Parse an uploaded Goodreads export CSV, stage its rows for review, and kick a
    background pass that classifies kind (fiction/nonfiction) + genre (cheap Sonnet).
    Returns the parse summary + how many rows were staged / skipped as duplicate, and
    whether background enrichment was scheduled (the client then polls
    GET /api/import/status). IMPORT_AUTOENRICH=0 disables the auto-classify."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    text = payload.csv_text or ""
    if len(text) > _MAX_CSV_CHARS:
        raise HTTPException(
            status_code=413,
            detail="CSV is too large — export a smaller library or split the file.")
    try:
        rows, summary = goodreads_import.parse_goodreads_csv(text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV: {e}")
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="No importable rows found. Is this a Goodreads library export CSV?")
    try:
        result = db_write.stage_import_rows(user_id, rows, source="goodreads")
    except Exception as e:
        raise _server_error(e)
    enriching = bool(result["staged"]) and os.environ.get("IMPORT_AUTOENRICH", "1") != "0"
    if enriching:
        background_tasks.add_task(
            import_enrich.enrich_pending, user_id, batch_id=result["batch_id"])
    return {"ok": True, "parse": summary, "enriching": enriching, **result}


@app.get("/api/import/staging")
def list_import_staging(request: Request,
                        batch_id: Optional[str] = None,
                        shelf: Optional[str] = None,
                        state: Optional[str] = None,
                        user_id: str = Depends(auth.get_current_user_id)):
    """The caller's import-staging rows (review buffer + read-shelf ranking
    backlog), with per-shelf counts. Filterable by batch / shelf / state."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    rows = db_write.get_staging_rows(
        user_id, batch_id=batch_id, shelf=shelf, state=state)
    by_shelf, by_enrich = {}, {}
    for r in rows:
        by_shelf[r["shelf"]] = by_shelf.get(r["shelf"], 0) + 1
        by_enrich[r["enrich_state"]] = by_enrich.get(r["enrich_state"], 0) + 1
    return {"rows": rows, "count": len(rows),
            "by_shelf": by_shelf, "by_enrich": by_enrich}


@app.get("/api/import/status")
def import_status(request: Request, batch_id: Optional[str] = None,
                  user_id: str = Depends(auth.get_current_user_id)):
    """Cheap progress counts for polling an import while background enrichment runs:
    {total, by_state, by_enrich}. Enrichment is done when by_enrich has no 'pending'."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    return db_write.staging_status(user_id, batch_id=batch_id)


@app.post("/api/import/commit")
def commit_import(payload: ImportCommitRequest, request: Request,
                  user_id: str = Depends(auth.get_current_user_id),
                  user_md: dict = Depends(auth.get_current_user_metadata)):
    """Fan reviewed staging rows into the library: to-read + currently-reading become
    recommendations (predicted later); read rows stay as the ranking backlog. Rows
    missing kind/genre are skipped and reported (they stay in staging to fix).
    Currently-reading is treated as a to-read recommendation here — the in-progress
    marker is a client-side reading-status concern (localStorage), not a stored field.

    Also derives the reader's star-based per-genre taste offsets HERE, which is the only
    correct moment: the `read` rows carry `goodreads_rating`, and ranking a book deletes
    its staging row — so waiting until later would find the ratings gone."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = db_write.commit_staged(
                user_id, batch_id=payload.batch_id, ids=payload.ids)
    except Exception as e:
        raise _server_error(e)
    return {"ok": True, **result, "genre_offsets": _refresh_genre_offsets(user_id, user_md)}


@app.put("/api/import/staging/{staging_id}")
def update_import_staging(staging_id: str, payload: StagingRowUpdate, request: Request,
                          user_id: str = Depends(auth.get_current_user_id)):
    """Apply a review edit (genre / kind / series / …) to one staging row. Only
    the provided fields change. 404 if the row isn't the caller's."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    fields = payload.model_dump(exclude_unset=True)
    try:
        row = db_write.update_staging_row(user_id, staging_id, fields)
    except db_write.ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _server_error(e)
    if row is None:
        raise HTTPException(status_code=404, detail="Staging row not found.")
    return row


@app.delete("/api/import/staging/{staging_id}")
def delete_import_staging(staging_id: str, request: Request,
                          user_id: str = Depends(auth.get_current_user_id)):
    """Drop one staging row (discard a book from the import)."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    if not db_write.delete_staging_row(user_id, staging_id):
        raise HTTPException(status_code=404, detail="Staging row not found.")
    return {"ok": True}


@app.delete("/api/import/batch/{batch_id}")
def delete_import_batch(batch_id: str, request: Request,
                        user_id: str = Depends(auth.get_current_user_id)):
    """Discard an entire import batch."""
    _rate_limit(request, "import", **_RL_IMPORT, user_id=user_id)
    return {"ok": True, "deleted": db_write.clear_staging_batch(user_id, batch_id)}


# ── Public directory (every is_public profile, with book counts) ──
@app.get("/api/profiles/directory")
def get_profiles_directory(request: Request,
                           viewer_id: str = Depends(auth.get_current_user_id)):
    """Browsable list of every public profile. Signed-in only; viewer-keyed rate
    limit. Book counts come from a cheap COUNT (no engine warm-up per tenant)."""
    _rate_limit(request, "profile", **_RL_PROFILE, user_id=viewer_id)
    out = []
    for p in db_write.list_public_profiles():
        tuid = p["user_id"]
        out.append({
            "handle": p["handle"],
            "display_name": p["display_name"],
            "fiction_books": _book_count("books", tuid),
            "nonfiction_books": _book_count("nonfiction_books", tuid),
        })
    return {"profiles": out}


# ── One public profile's header (identity + counts) ──
@app.get("/api/users/{handle}")
def get_user_profile(handle: str, request: Request,
                     viewer_id: str = Depends(auth.get_current_user_id)):
    """Header for one public profile: handle, display name, and library sizes."""
    tuid = _cross_user_target(handle, request, viewer_id)
    prof = db_write.get_profile_by_handle(handle)
    return {
        "handle": prof["handle"],
        "display_name": prof["display_name"],
        "fiction_books": _book_count("books", tuid),
        "nonfiction_books": _book_count("nonfiction_books", tuid),
    }


# ── Cross-user data: thin delegations to the existing tenant-scoped handlers,
#    called with the TARGET's user_id. A `kind` query param selects the track. ──
@app.get("/api/users/{handle}/books")
def get_user_books(handle: str, request: Request, kind: str = "fiction",
                   viewer_id: str = Depends(auth.get_current_user_id)):
    tuid = _cross_user_target(handle, request, viewer_id)
    return get_nf_books(user_id=tuid) if kind == "nonfiction" else get_books(user_id=tuid)


@app.get("/api/users/{handle}/tiers")
def get_user_tiers(handle: str, request: Request, kind: str = "fiction",
                   year: Optional[int] = None,
                   viewer_id: str = Depends(auth.get_current_user_id)):
    tuid = _cross_user_target(handle, request, viewer_id)
    if kind == "nonfiction":
        return get_nf_tiers(user_id=tuid)   # nonfiction has no year filter
    return get_tiers(year=year, user_id=tuid)


@app.get("/api/users/{handle}/read-queue")
def get_user_read_queue(handle: str, request: Request, kind: str = "fiction",
                        viewer_id: str = Depends(auth.get_current_user_id)):
    tuid = _cross_user_target(handle, request, viewer_id)
    if kind == "nonfiction":
        return get_nf_read_queue(user_id=tuid)
    # user_md={}: the target's JWT preferences (word-count/fav-authors) aren't
    # reachable cross-user. Data-rich targets use their FITTED cold-start term, so
    # this is a no-op for them; a cold-start target just loses the preference nudge.
    return get_read_queue(user_id=tuid, user_md={})


@app.get("/api/users/{handle}/stats")
def get_user_stats(handle: str, request: Request,
                   viewer_id: str = Depends(auth.get_current_user_id)):
    """Combined fiction+nonfiction stats for the target — the same payload the
    owner's own /stats page uses (also carries combined_ranking for the profile's
    'All' rankings toggle)."""
    tuid = _cross_user_target(handle, request, viewer_id)
    return get_combined_stats(user_id=tuid)
