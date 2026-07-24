"""
walkforward.py
==============
Chronological walk-forward backtest of the researched-prediction engine.

THE QUESTION THIS ANSWERS
-------------------------
"What would the engine have predicted for each book on the day I started it,
using only what was known then?" -- an HONEST accuracy baseline that future
engine features must beat, plus the raw dataset for a future track-record page.

This is stricter than the leave-one-out sweep in validate_engine.py. LOO trains
on the other 126 books INCLUDING future ones; walk-forward trains only on books
read BEFORE the held-out book, in real reading order (the Timeline sheet).

WHAT IT IS (and is NOT)
-----------------------
  * It CALLS the read-only engine; it never modifies prediction math.
    predict_engine / db_loader / reresearch_and_measure / research_predict are
    used exactly as the live Predict page uses them (research vector -> optional
    correlation-smoothing -> author+genre correction -> WA roll-up).
  * It NEVER writes books.db. Every fold is an in-memory filter of the books
    DataFrame -- no scratch DB file needed, because the researched-prediction
    functions are all parameterised by the `books` frame + `cache` dict (there
    is no hardcoded DB read inside the prediction path). See PHASE-0 NOTES.
  * ZERO API SPEND, structurally. The harness reads the richer-prompt cache
    (llm_scores_richer.json) as a plain dict and calls only the pure correction
    functions. It monkeypatches anthropic.Anthropic to raise, so any accidental
    client construction fails loudly rather than spending a cent. A book with no
    usable cache entry is logged SKIPPED_NO_CACHE -- never researched.
  * DETERMINISTIC. Same DB + caches -> byte-identical folds artifact (sorted
    keys, fixed float rounding, no timestamps in the folds file).

THREE VARIANTS PER FOLD (all run, all from cache)
-------------------------------------------------
  raw    -- research vector rolled straight to WA: no correlation smoothing, no
            author+genre correction. "How good is the uncalibrated grounded LLM
            research?" Pool-independent by construction.
  honest -- full pipeline (smooth + author_genre correction), every trainable
            piece (correction pairs, smoothing models, resid_sd, rank) fit on
            the PAST-ONLY pool (positions 1..t-1). THE walk-forward baseline.
  leaky  -- same pipeline, but fit on the FULL library (today's config). Labeled
            leaky because the correction saw future books. "How good is today's
            engine config", not "what was knowable then".

PHASE-0 NOTES (leakage + architecture facts this harness relies on)
-------------------------------------------------------------------
  * The DeltaTracker `component_corrections` table is RETIRED (active row has
    all-zero constants, blend 0) AND is never read by the prediction path, so it
    does not enter any variant. The correction that actually shapes predictions
    is reresearch_and_measure.correct_book (method "author_genre").
  * Research-cache vectors embed post-publication reception -- an ACCEPTED
    hindsight caveat (a walk-forward run cannot un-know a book's reputation).
  * The interval recorded per variant is the engine's own +/-1.645*resid_sd
    (a ~90% normal interval, exactly what correct_and_predict emits), NOT the
    served density-bucketed conformal interval (that needs a full-LOO residual
    table and is out of scope here).

SPLIT MODES (Phase 1.2)
-----------------------
Reading order is the DB read_seq (native; supersedes the hand-edited, drift-prone
Excel Timeline). Three fold-split modes, all keeping the walk-forward past-only
pool and differing only in which earlier books are allowed in it:
  time    -- every earlier book (the original baseline; default).
  author  -- earlier books minus the target's author (cold-start-by-author).
  series  -- earlier books minus the target's series ('Standalone' is not a group).

HOW TO RUN
----------
    python3 walkforward.py                    # time mode: folds + report (+ rank metrics)
    python3 walkforward.py --split-mode author  # one grouped mode -> validation/splits/author/
    python3 walkforward.py --all-splits       # every mode + validation/walkforward_splits.md
    python3 walkforward.py --report-only      # rebuild report from existing folds
    python3 walkforward.py --check-determinism  # prove two runs are identical
    python3 walkforward.py --burn-in 15       # min TRAINING-POOL size before evaluating

Artifacts land in validation/ (NOT a static-snapshot input -- see README). Grouped
modes write under validation/splits/<mode>/; the time mode keeps the canonical
filenames that track_record.py reads.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import db_backend
import subprocess

import numpy as np
import pandas as pd
from scipy import stats

# Read-only engine + the exact live-Predict glue. Importing research_predict
# pulls in `anthropic`, but NO client is constructed and NO network call is made
# here -- we call only its pure functions and guard the client below.
import predict_engine as pe
import db_loader
import reresearch_and_measure as rm
import research_predict as rp

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE = rm.LIVE                       # canonical 14 components, reference order
WB = set(rm.WB)                      # the 3 worldbuilding comps (0.0 sentinel)
OUT_DIR = os.path.join(ROOT, "validation")
FOLDS_FILE = "walkforward_folds.jsonl"
META_FILE = "walkforward_meta.json"
REPORT_FILE = "walkforward_report.md"
ROLLING_FILE = "walkforward_rolling_mae.json"

BURN_IN_DEFAULT = 15                 # min training-pool size before we evaluate
ROLL_WINDOW = 15                     # rolling-MAE window (folds)
NOMINAL_COVERAGE = 0.90              # +/-1.645*resid_sd is a 90% normal interval
VARIANTS = ("raw", "honest", "leaky", "hybrid")
GROUNDED_CACHE = "web_grounded_cache.json"   # the 2nd research method (web-grounded)

# Split modes (brief Phase 1.2). ALL three keep the walk-forward "no future
# books" honesty -- the training pool is always positions strictly earlier in
# reading order. They differ only in which earlier books are ALLOWED in the pool:
#   time   -- every earlier book (the original walk-forward baseline; default).
#   author -- earlier books MINUS any by the target's author (cold-start-by-author:
#             "predict this book as if I'd never read this author").
#   series -- earlier books MINUS any in the target's series. "Standalone" and
#             empty series are NOT a group (a standalone has no series-mates to
#             memorise), so a standalone target's pool equals the time pool.
# Holding the temporal structure fixed and varying ONLY the grouping means the
# MAE delta is attributable to same-author/same-series memorisation, not to a
# different train/test regime.
SPLIT_MODES = ("time", "author", "series")
SPLITS_DIR = os.path.join(OUT_DIR, "splits")   # grouped-mode artifacts (isolated
#   from the canonical time-mode files that track_record.py reads)
SPLITS_TABLE_FILE = "walkforward_splits.md"

# Verbatim into the results metadata (brief Phase 0.2). "neutralised" == a
# past-only pool filter removes the future-information leak for that input.
LEAKAGE_INVENTORY = {
    "author_genre_correction_pairs": "neutralised in honest (pool), LEAKY in leaky (full library)",
    "correlation_smoothing_models": "neutralised in honest (pool), LEAKY in leaky (full library)",
    "resid_sd_for_interval": "neutralised in honest (pool), LEAKY in leaky (full library)",
    "rank_over_library": "neutralised in honest (pool); leaky ranks over full library",
    "genre_and_component_weights": "config, not learned from the book set -- no leakage",
    "component_corrections_deltatracker": "retired to zero AND unwired -- enters no variant",
    "research_cache_vector": "ACCEPTED hindsight caveat -- embeds post-publication reception",
}


# ---------------------------------------------------------------------------
# Zero-API structural guard
# ---------------------------------------------------------------------------
def _install_no_api_guard():
    """Make the harness structurally incapable of spending tokens: replace the
    Anthropic client constructor so ANY accidental client build raises. The
    prediction functions we call (correct_and_predict / build_corr_models /
    correct_book / _wa_from_components) never construct a client, so this is a
    belt-and-braces backstop, not a behavioural change."""
    import anthropic

    def _blocked(*_a, **_k):
        raise RuntimeError(
            "walkforward.py is zero-spend: Anthropic client construction is "
            "blocked. A cache miss must be logged SKIPPED_NO_CACHE, not researched.")

    anthropic.Anthropic = _blocked


# ---------------------------------------------------------------------------
# Determinism helpers
# ---------------------------------------------------------------------------
def _r(x):
    """Round to a fixed precision and coerce numpy -> python float, so two runs
    serialise byte-identically. None passes through."""
    if x is None:
        return None
    if isinstance(x, (int, np.integer)) and not isinstance(x, bool):
        return int(x)
    return round(float(x), 6)


def _rd(d):
    """Round every value of a component dict deterministically."""
    return {c: _r(d.get(c)) for c in LIVE}


def _git_head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def _engine_hash():
    """Content hash of every file whose code determines a prediction. Changes if
    the engine or its glue changes, so a stale folds artifact is detectable."""
    h = hashlib.sha256()
    for name in ("predict_engine.py", "db_loader.py",
                 "reresearch_and_measure.py", "research_predict.py"):
        try:
            with open(os.path.join(ROOT, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"\0MISSING\0")
    return "sha256:" + h.hexdigest()[:16]


def _active_correction_version(db_path):
    """Read (read-only) the active component_corrections version, for the meta
    header. Returns a short descriptor even though this layer enters no variant
    (it is retired + unwired) -- recording it documents that fact per run."""
    try:
        uri = "file:" + os.path.abspath(db_path) + "?mode=ro"
        con = db_backend.connect(uri, uri=True)
        row = con.execute(
            "SELECT version, decision FROM component_corrections "
            "WHERE active=1 LIMIT 1").fetchone()
        con.close()
        if row:
            return {"version": row[0], "decision": row[1],
                    "applied_in_engine": False}
    except Exception:
        pass
    return {"version": None, "decision": None, "applied_in_engine": False}


# ---------------------------------------------------------------------------
# Ordering (native DB read_seq — supersedes the now-stale Excel Timeline)
# ---------------------------------------------------------------------------
# The reading order used to come from the workbook's Timeline sheet, but that
# sheet is edited by hand and had drifted behind books.db (it missed the 4
# most-recently-logged reads, which were then wrongly "placed last"). The DB now
# carries a native, clean read_seq (an integer reading-order rank, higher = more
# recent; set on add and editable via db_write.set_read_seq) for every book, so
# we order by it directly. This removes the openpyxl dependency AND the drift.
def _db_order_map(db_path):
    """Read {title: (read_seq, series_number)} from books.db (read-only). Both
    columns live in the DB but db_loader's frame surfaces neither."""
    out = {}
    try:
        uri = "file:" + os.path.abspath(db_path) + "?mode=ro"
        con = db_backend.connect(uri, uri=True)
        for t, seq, sn in con.execute(
                "SELECT title, read_seq, series_number FROM books WHERE user_id=?",
                (db_backend.DEFAULT_USER_ID,)):
            out[str(t).strip()] = (seq, sn)
        con.close()
    except Exception:
        pass
    return out


def build_order(books, xlsx_path=None, db_path=None):
    """Return (ordered_positions, skips).

    ordered_positions: one dict per rated fiction book, in reading order, each
    carrying a 1-based position + DB metadata (author/genre/series/series_number/
    year_read/read_seq). Order is the DB read_seq ascending (oldest first); any
    book with a null read_seq (none under current data) sorts last by title, for
    determinism. skips is always [] (every book has a position now).

    `xlsx_path` is accepted and IGNORED — kept only so older experiment callers
    that pass a workbook path positionally keep working. Order comes from
    read_seq, which supersedes the Excel Timeline."""
    db_path = db_path or db_loader.DB
    meta = _db_order_map(db_path)
    db_by_title = {row["Book"]: row for _, row in books.iterrows()}

    def _key(t):
        seq = meta.get(t, (None, None))[0]
        return (seq is None, seq if seq is not None else 0, t)

    positions = []
    for pos, title in enumerate(sorted(db_by_title, key=_key), start=1):
        row = db_by_title[title]
        seq, sn = meta.get(title, (None, None))
        positions.append({
            "position": pos,
            "title": title,
            "author": row["Author"],
            "genre": row["Genre"],
            "series": (row["Series"] or None),
            "series_number": sn,
            "year_read": int(row["Year"]) if row["Year"] is not None else None,
            "read_seq": int(seq) if seq is not None else None,
        })
    return positions, []


def _pool_titles(order, idx, split_mode):
    """Training-pool titles for the fold at order[idx], under `split_mode`.

    Always past-only (positions strictly earlier than the target's) — the
    walk-forward invariant. Grouped modes then drop same-group earlier books.
    'Standalone'/empty series is not a group (see SPLIT_MODES)."""
    target = order[idx]
    past = order[:idx]                      # positions 1..idx == strictly earlier
    if split_mode == "author":
        past = [e for e in past if e["author"] != target["author"]]
    elif split_mode == "series":
        s = (target["series"] or "").strip()
        if s and s.lower() != "standalone":
            past = [e for e in past if (e["series"] or "").strip() != s]
    return [e["title"] for e in past]


# ---------------------------------------------------------------------------
# Variant prediction
# ---------------------------------------------------------------------------
def _errors(pred_components, pred_wa, actual_components, actual_wa):
    """Signed + absolute error for WA and every component."""
    comp_signed, comp_abs = {}, {}
    for c in LIVE:
        a = actual_components.get(c)
        p = pred_components.get(c)
        if a is None or p is None or (isinstance(a, float) and np.isnan(a)):
            comp_signed[c], comp_abs[c] = None, None
        else:
            comp_signed[c] = _r(p - a)
            comp_abs[c] = _r(abs(p - a))
    return {
        "wa_signed_error": _r(pred_wa - actual_wa),
        "wa_abs_error": _r(abs(pred_wa - actual_wa)),
        "component_signed_error": comp_signed,
        "component_abs_error": comp_abs,
    }


def _variant_raw(raw_scores, genre, gw, gcw, resid_sd, actual_components, actual_wa):
    """RAW: research vector -> WA, no smoothing, no correction. Pool-independent
    components; borrows the honest pool resid_sd only to state an interval."""
    wa = rp._wa_from_components(raw_scores, genre, gw, gcw)
    half = 1.645 * resid_sd
    rec = {
        "wa": _r(wa), "components": _rd(raw_scores),
        "ci_low": _r(wa - half), "ci_high": _r(wa + half),
        "ci_inside": bool(wa - half <= actual_wa <= wa + half),
        "resid_sd": _r(resid_sd), "rank": None, "rank_total": None,
        "n_author": None, "n_genre": None, "analog_src": "none",
    }
    rec.update(_errors(raw_scores, wa, actual_components, actual_wa))
    return rec


def _variant_corrected(title, author, genre, raw_scores, conf, books_train,
                       resid_sd, corr_models, gw, gcw, cache,
                       actual_components, actual_wa):
    """HONEST or LEAKY (identical code; the caller decides the training frame).
    Runs the exact live pipeline: correlation-smooth -> author+genre correct ->
    WA roll-up, via research_predict.correct_and_predict."""
    res = rp.correct_and_predict(
        title, author, genre, dict(raw_scores), conf, resid_sd,
        books_train, gw, gcw, cache, corr_models=corr_models)
    wa = res["wa"]
    ci_low, ci_high = res["ci"]
    n_author, n_genre = res["n_author"], res["n_genre"]
    rec = {
        "wa": _r(wa), "components": _rd(res["scores"]),
        "ci_low": _r(ci_low), "ci_high": _r(ci_high),
        "ci_inside": bool(ci_low <= actual_wa <= ci_high),
        "resid_sd": _r(resid_sd), "rank": res["rank"], "rank_total": res["total"],
        "n_author": n_author, "n_genre": n_genre,
        "analog_src": ("author" if n_author > 0
                       else "genre" if n_genre > 0 else "global"),
    }
    rec.update(_errors(res["scores"], wa, actual_components, actual_wa))
    return rec


def _load_hybrid_targets(cache, root=ROOT):
    """Build the LIVE hybrid research vector per book: the memory (richer) vector
    with the policy's grounded components overridden by the web-grounded cache —
    exactly what the app serves after the background grounded-upgrade
    (hybrid_researcher.apply_grounded_overrides). This is the SERVED-settled input
    the reader ultimately sees; the memory-only vector is the pre-refine state.
    Returns {title: {14 comps}}; a book absent from the grounded cache keeps its
    memory vector (no override), mirroring the live per-component fallback."""
    try:
        import hybrid_researcher as _hyb
        gcomps = set(_hyb.grounded_components())
    except Exception:      # keep the harness runnable if the module is unavailable
        gcomps = {"Depth", "Depth2", "Ending", "Insights", "Integration", "Originality"}
    try:
        with open(os.path.join(root, GROUNDED_CACHE)) as fh:
            grounded = json.load(fh)
    except (OSError, ValueError):
        grounded = {}
    out = {}
    for t, entry in cache.items():
        sc = entry.get("scores") if isinstance(entry, dict) else None
        if not isinstance(sc, dict) or not all(c in sc for c in LIVE):
            continue
        gsc = (grounded.get(t) or {}).get("scores", {}) or {}
        out[t] = {c: (float(gsc[c]) if (c in gcomps and c in gsc) else float(sc[c]))
                  for c in LIVE}
    return out


def run_folds(books, gw, gcw, cache, order, burn_in, split_mode="time",
              hybrid_targets=None):
    """Walk the reading order, predicting each book from its past-only pool under
    `split_mode` (see SPLIT_MODES). A fold is evaluated iff its training pool
    holds >= burn_in books; else it is skipped POOL_LT_BURN_IN. For split_mode
    'time' this pool-size gate is identical to the old pos<=burn_in gate (pool ==
    pos-1), so the time baseline is unchanged in structure. Returns (folds, skips).

    The 'raw' variant is pool-independent (research vector -> WA), so its WA is
    identical across split modes — a built-in sanity check. Only honest (pool
    correction) and leaky (full-library correction) move with the pool. leaky is
    the fixed 'today's config' reference and is the SAME in every mode."""
    # LEAKY config is the SAME for every fold ("today's engine"): full-library
    # smoothing models + resid_sd, computed once. correct_and_predict excludes
    # the target row from the correction training internally.
    resid_sd_full = pe.fit_regression(books)[2]
    corr_models_full = rp.build_corr_models(books, cache)

    folds, skips = [], []
    title_to_row = {row["Book"]: row for _, row in books.iterrows()}

    for idx, entry in enumerate(order):
        pos = entry["position"]
        title = entry["title"]

        if title not in cache or not isinstance(cache[title].get("scores"), dict):
            skips.append({"skip": True, "position": pos, "title": title,
                          "reason": "SKIPPED_NO_CACHE", "split_mode": split_mode})
            continue

        raw_scores = {c: float(cache[title]["scores"][c])
                      for c in LIVE if c in cache[title]["scores"]}
        if len(raw_scores) != len(LIVE):
            skips.append({"skip": True, "position": pos, "title": title,
                          "reason": "SKIPPED_NO_CACHE", "split_mode": split_mode})
            continue

        # Past-only pool under this split mode (grouped modes drop same-group).
        pool_titles = _pool_titles(order, idx, split_mode)
        books_pool = books[books["Book"].isin(pool_titles)]
        if len(books_pool) < burn_in:
            skips.append({"skip": True, "position": pos, "title": title,
                          "reason": "POOL_LT_BURN_IN", "pool_size": int(len(books_pool)),
                          "split_mode": split_mode})
            continue

        conf = cache[title].get("conf", "?")
        row = title_to_row[title]
        actual_wa = float(row["WA"])
        actual_components = {c: (float(row[c]) if row[c] is not None
                                 and not (isinstance(row[c], float) and np.isnan(row[c]))
                                 else None) for c in LIVE}
        author, genre = entry["author"], entry["genre"]

        resid_sd_pool = pe.fit_regression(books_pool)[2]
        corr_models_pool = rp.build_corr_models(books_pool, cache)

        # The 'hybrid' variant is the LIVE SERVED input: memory correction +
        # smoothing (identical to honest) applied to the HYBRID target vector
        # (memory + the policy's grounded overrides). ONLY the target vector differs
        # from honest — exactly how backend/main.py serves the grounded prediction
        # (memory cache/corr, hybridised scores). This is the app's real accuracy;
        # honest is the pre-refine (memory-only) state.
        hyb_scores = raw_scores
        if hybrid_targets and title in hybrid_targets:
            hyb_scores = {c: float(hybrid_targets[title][c]) for c in LIVE}
        variants = {
            "raw": _variant_raw(raw_scores, genre, gw, gcw, resid_sd_pool,
                                actual_components, actual_wa),
            "honest": _variant_corrected(
                title, author, genre, raw_scores, conf, books_pool,
                resid_sd_pool, corr_models_pool, gw, gcw, cache,
                actual_components, actual_wa),
            "leaky": _variant_corrected(
                title, author, genre, raw_scores, conf, books,
                resid_sd_full, corr_models_full, gw, gcw, cache,
                actual_components, actual_wa),
            "hybrid": _variant_corrected(
                title, author, genre, hyb_scores, conf, books_pool,
                resid_sd_pool, corr_models_pool, gw, gcw, cache,
                actual_components, actual_wa),
        }

        folds.append({
            "position": pos, "title": title, "author": author, "genre": genre,
            "series": entry["series"], "series_number": entry["series_number"],
            "year_read": entry["year_read"], "read_seq": entry.get("read_seq"),
            "split_mode": split_mode,
            "pool_size": int(len(books_pool)), "cache_key": title,
            "actual_wa": _r(actual_wa), "actual_components": _rd(actual_components),
            "variants": variants,
        })

    folds.sort(key=lambda f: f["position"])
    skips.sort(key=lambda s: (s["position"] if s["position"] is not None else 0,
                              s["title"]))
    return folds, skips


# ---------------------------------------------------------------------------
# Artifact I/O
# ---------------------------------------------------------------------------
def _serialise_folds(folds, skips):
    """Deterministic JSONL text: fold records (by position) then skip records.
    No timestamps -> byte-identical across runs on the same DB + caches."""
    lines = [json.dumps(f, sort_keys=True) for f in folds]
    lines += [json.dumps(s, sort_keys=True) for s in skips]
    return "\n".join(lines) + "\n"


def write_artifacts(folds, skips, books, cache, order, burn_in, out_dir,
                    split_mode="time"):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, FOLDS_FILE), "w") as fh:
        fh.write(_serialise_folds(folds, skips))

    import datetime
    skip_counts = {}
    for s in skips:
        skip_counts[s["reason"]] = skip_counts.get(s["reason"], 0) + 1
    meta = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_head": _git_head(),
        "engine_hash": _engine_hash(),
        "correction_version_active": _active_correction_version(db_loader.DB),
        "split_mode": split_mode,
        "order_source": "read_seq",
        "burn_in": burn_in,
        "n_books_total": len(order),
        "n_folds_evaluated": len(folds),
        "n_skipped": len(skips),
        "skip_reasons": skip_counts,
        "n_in_cache": sum(1 for e in order if e["title"] in cache),
        "components": LIVE,
        "variants": {
            "raw": "research vector -> WA (no smoothing, no correction); pool-independent",
            "honest": "MEMORY-only vector, smooth + author_genre correction fit on PAST-ONLY pool (pre-refine state)",
            "leaky": "smooth + author_genre correction fit on FULL library (today's config; leaky)",
            "hybrid": "LIVE SERVED input: memory correction (as honest) on the HYBRID vector (memory + web-grounded overrides) -- what the app serves after the grounded-upgrade",
        },
        "nominal_interval_coverage": NOMINAL_COVERAGE,
        "interval_note": "per-variant interval is the engine's +/-1.645*resid_sd, not the served conformal interval",
        "leakage_inventory": LEAKAGE_INVENTORY,
        "caveats": [
            "research-cache vectors embed post-publication reception (hindsight) -- accepted",
            "leaky variant's correction saw future books -- labeled leaky, not a knowable-then number",
            "reading order is the DB read_seq (native, supersedes the now-stale Excel Timeline)",
            "grouped split modes (author/series) drop same-group earlier books from the pool; a "
            "fold whose grouped pool < burn_in is skipped POOL_LT_BURN_IN",
        ],
    }
    with open(os.path.join(out_dir, META_FILE), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return meta


def load_folds(out_dir):
    """Read back the folds artifact -> (folds, skips), so --report-only works
    standalone from a prior run."""
    folds, skips = [], []
    path = os.path.join(out_dir, FOLDS_FILE)
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            (skips if rec.get("skip") else folds).append(rec)
    folds.sort(key=lambda f: f["position"])
    return folds, skips


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_inputs():
    books, gw, gcw = db_loader.load_from_db()
    cache = rp.load_cache()
    hybrid_targets = _load_hybrid_targets(cache)
    return books, gw, gcw, cache, hybrid_targets


def do_run(burn_in, out_dir, split_mode="time"):
    _install_no_api_guard()
    books, gw, gcw, cache, hyb = _load_inputs()
    order, _ = build_order(books)
    folds, skips = run_folds(books, gw, gcw, cache, order, burn_in,
                             split_mode=split_mode, hybrid_targets=hyb)
    meta = write_artifacts(folds, skips, books, cache, order, burn_in, out_dir,
                           split_mode=split_mode)
    print(f"Walk-forward [{split_mode}]: {meta['n_folds_evaluated']} folds evaluated, "
          f"{meta['n_skipped']} skipped ({meta['skip_reasons']}).")
    print(f"  wrote {os.path.join(out_dir, FOLDS_FILE)}")
    print(f"  wrote {os.path.join(out_dir, META_FILE)}")
    return folds, skips


def do_check_determinism(burn_in, split_mode="time"):
    _install_no_api_guard()
    books, gw, gcw, cache, hyb = _load_inputs()
    order, _ = build_order(books)
    a = _serialise_folds(*run_folds(books, gw, gcw, cache, order, burn_in,
                                    split_mode=split_mode, hybrid_targets=hyb))
    b = _serialise_folds(*run_folds(books, gw, gcw, cache, order, burn_in,
                                    split_mode=split_mode, hybrid_targets=hyb))
    ha, hb = hashlib.sha256(a.encode()).hexdigest(), hashlib.sha256(b.encode()).hexdigest()
    print(f"run A sha256: {ha}")
    print(f"run B sha256: {hb}")
    if a == b:
        print("DETERMINISM: PASS (two runs byte-identical)")
        return True
    print("DETERMINISM: FAIL")
    return False


# ---------------------------------------------------------------------------
# Rank metrics (Phase 1.1) + split-mode comparison table (Phase 1.3)
# ---------------------------------------------------------------------------
def variant_metrics(folds, variant):
    """Pooled out-of-fold WA MAE + rank correlation (Spearman rho, Kendall tau)
    of predicted vs actual WA for one variant over `folds`. The product ranks
    books, so a biased-but-order-preserving model can beat a lower-MAE noisier
    one — rank correlation is a first-class adoption metric alongside MAE."""
    pred = [f["variants"][variant]["wa"] for f in folds]
    act = [f["actual_wa"] for f in folds]
    ae = [f["variants"][variant]["wa_abs_error"] for f in folds
          if f["variants"][variant]["wa_abs_error"] is not None]
    n = len(folds)
    mae = (sum(ae) / len(ae)) if ae else None
    rho = float(stats.spearmanr(pred, act)[0]) if n >= 3 else None
    tau = float(stats.kendalltau(pred, act)[0]) if n >= 3 else None
    return {"n": n, "mae": mae, "spearman": rho, "kendall": tau}


def _fmt_metric(x, p=3):
    return " - " if x is None else f"{x:.{p}f}"


def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _render_splits_table(results, burn_in):
    """results: {split_mode: folds}. Emits the Phase-1 baseline comparison."""
    L = ["# Walk-Forward — Split-Mode Baseline (Phase 1)\n"]
    L.append(f"Reading order = DB `read_seq` · burn-in {burn_in} · "
             f"engine `{_engine_hash()}` · git `{(_git_head() or '')[:12]}`.\n")
    L.append("All modes keep the walk-forward past-only pool; grouped modes additionally "
             "drop same-author / same-series earlier books. **raw** is pool-independent "
             "(WA identical in every mode) and **leaky** is the fixed full-library "
             "reference (identical in every mode) — the **honest** rows carry the signal.\n")

    hy = {mode: variant_metrics(results[mode], "hybrid") for mode in SPLIT_MODES}
    hm = {mode: variant_metrics(results[mode], "honest") for mode in SPLIT_MODES}
    L.append("## Live-served accuracy (hybrid) by split mode  — THE REAL BASELINE\n")
    L.append("The app serves the hybrid vector (memory + web-grounded overrides) after "
             "the background grounded-upgrade, so this is what a reader actually gets. "
             "`honest` below is the memory-only pre-refine state (what earlier reports "
             "called the baseline; it understates live accuracy).\n")
    L.append(_md_table(
        ["split mode", "n folds", "hybrid WA MAE", "ρ", "τ", "honest (memory) MAE"],
        [[mode, hy[mode]["n"], _fmt_metric(hy[mode]["mae"]),
          _fmt_metric(hy[mode]["spearman"]), _fmt_metric(hy[mode]["kendall"]),
          _fmt_metric(hm[mode]["mae"])]
         for mode in SPLIT_MODES]))
    L.append("")

    L.append("## All variants × split mode  (WA MAE / ρ / τ)\n")
    rows = []
    for v in VARIANTS:
        for mode in SPLIT_MODES:
            m = variant_metrics(results[mode], v)
            rows.append([v, mode, m["n"], _fmt_metric(m["mae"]),
                         _fmt_metric(m["spearman"]), _fmt_metric(m["kendall"])])
    L.append(_md_table(["variant", "split mode", "n", "WA MAE", "ρ", "τ"], rows))
    L.append("")

    # Common-subset: books scored in EVERY mode, so the honest-MAE delta is
    # attributable to grouping, not to a different fold set (grouped modes skip
    # more folds when the grouped pool falls under burn-in).
    common = set.intersection(*[{f["title"] for f in results[m]} for m in SPLIT_MODES])
    L.append(f"## Honest variant on the common fold subset  (n={len(common)}; "
             "identical books scored in every mode)\n")
    crows = []
    for mode in SPLIT_MODES:
        sub = [f for f in results[mode] if f["title"] in common]
        m = variant_metrics(sub, "honest")
        crows.append([mode, m["n"], _fmt_metric(m["mae"]),
                      _fmt_metric(m["spearman"]), _fmt_metric(m["kendall"])])
    L.append(_md_table(["split mode", "n", "WA MAE", "Spearman ρ", "Kendall τ"], crows))
    L.append("\n_The common-subset table is the clean apples-to-apples read: identical "
             "books, only the training-pool grouping differs._")
    return "\n".join(L) + "\n"


def do_all_splits(burn_in):
    """Run every split mode, write per-mode artifacts (time -> the canonical
    validation/ files; author/series -> validation/splits/<mode>/), build the
    standard report for the canonical time run, and write the comparison table."""
    _install_no_api_guard()
    books, gw, gcw, cache, hyb = _load_inputs()
    order, _ = build_order(books)
    results = {}
    for mode in SPLIT_MODES:
        out_dir = OUT_DIR if mode == "time" else os.path.join(SPLITS_DIR, mode)
        folds, skips = run_folds(books, gw, gcw, cache, order, burn_in,
                                 split_mode=mode, hybrid_targets=hyb)
        write_artifacts(folds, skips, books, cache, order, burn_in, out_dir, split_mode=mode)
        results[mode] = folds
        sr = {}
        for s in skips:
            sr[s["reason"]] = sr.get(s["reason"], 0) + 1
        print(f"[{mode}] {len(folds)} folds evaluated, {len(skips)} skipped ({sr}) -> {out_dir}")
        if mode == "time":
            _maybe_build_report(out_dir, required=False)
    md = _render_splits_table(results, burn_in)
    with open(os.path.join(OUT_DIR, SPLITS_TABLE_FILE), "w") as fh:
        fh.write(md)
    print("\n" + md)
    print(f"  wrote {os.path.join(OUT_DIR, SPLITS_TABLE_FILE)}")
    return results


def main():
    ap = argparse.ArgumentParser(
        description="Chronological walk-forward backtest of the researched "
                    "prediction engine (zero-spend, read-only).")
    ap.add_argument("--burn-in", type=int, default=BURN_IN_DEFAULT,
                    help=f"min training-pool size before evaluating (default {BURN_IN_DEFAULT}).")
    ap.add_argument("--out-dir", default=OUT_DIR, help="artifact directory (time mode).")
    ap.add_argument("--split-mode", choices=SPLIT_MODES, default="time",
                    help="fold split: time (default walk-forward), author, or series.")
    ap.add_argument("--all-splits", action="store_true",
                    help=f"run every split mode + write the comparison table ({SPLITS_TABLE_FILE}).")
    ap.add_argument("--xlsx", default=None,
                    help="(deprecated, ignored) reading order now comes from the DB read_seq.")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from an existing folds artifact.")
    ap.add_argument("--check-determinism", action="store_true",
                    help="run the folds twice and assert byte-identical output.")
    args = ap.parse_args()

    if args.check_determinism:
        raise SystemExit(0 if do_check_determinism(args.burn_in, args.split_mode) else 1)

    if args.report_only:
        _maybe_build_report(args.out_dir, required=True)
        return

    if args.all_splits:
        do_all_splits(args.burn_in)
        return

    out_dir = (args.out_dir if args.split_mode == "time"
               else os.path.join(SPLITS_DIR, args.split_mode))
    do_run(args.burn_in, out_dir, args.split_mode)
    _maybe_build_report(out_dir, required=False)


def _maybe_build_report(out_dir, required):
    """Build the markdown report if the report module is present. It ships in a
    later commit than the core harness, so the harness stays runnable without it."""
    try:
        import walkforward_report as wr
    except ImportError:
        msg = ("walkforward_report.py not found -- run the harness first, then "
               "the report module (added in the report commit).")
        if required:
            raise SystemExit(msg)
        print(f"  (skipped report: {msg})")
        return
    wr.build_report(out_dir)


if __name__ == "__main__":
    main()
