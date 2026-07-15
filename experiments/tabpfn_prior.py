"""
experiments/tabpfn_prior.py  —  OFFLINE ONLY.  TabPFN cold-start prior challenger.
====================================================================================
Phase 1 of the "TabPFN-2.5 Cold-Start Prior (K_USER_PRIOR)" brief.

WHAT THIS IS
------------
A walk-forward challenger that, on the low-support ("cold-start") slice only,
replaces the engine's shrinkage prior mean (the honest author+genre correction's
WA) with a TabPFN in-context regression over the past-only pool. It is validated
against the SAME committed folds the champion uses, so the comparison is apples to
apples and Bands A/C are byte-identical to today by construction.

TWO CHALLENGER DESIGNS (--design)
---------------------------------
  wa_direct       TabPFN predicts the target's WA directly from [14 raw LLM
                  components + genre code]. Simplest, but forces the model to
                  re-learn the genre-specific component->WA weighting.
  component_wise  TabPFN predicts each of the 14 ACTUAL components (LLM->actual
                  mapping, the same job the correction does), then the engine's
                  EXACT _wa_from_components rolls them up with the target's genre
                  weights. Factors out the weighting; a fairer test of what a
                  foundation model adds over the hand-built correction. WB
                  components exclude the realist-genre 0.0 sentinel rows from
                  their training pool, exactly as reresearch_and_measure does.

HARD BOUNDARIES (see CLAUDE.md)
-------------------------------
  * OFFLINE ONLY. Lives in experiments/; NEVER imported by predict_engine.py,
    backend/main.py, or the serve path. It imports the read-only engine glue
    (db_loader / research_predict / reresearch_and_measure / walkforward) and
    reimplements NO prediction math — the champion honest WA it compares against
    is read verbatim from validation/walkforward_folds.jsonl, and the WA roll-up
    is the engine's own research_predict._wa_from_components.
  * NO WRITES. Never touches books.db / db_write. (Phase 3 would add a precomputed
    column via db_write — not here.)
  * The served conformal interval is untouched: this swaps the point prior mean only.

RUNTIME
-------
Isolated .venv-tabpfn25 (tabpfn==8.0.7, Python 3.9). TabPFN-2.5 weights are
license-gated: set TABPFN_TOKEN (one-time free Prior Labs license) for --model v2.5.
--model v2 (TabPFN-v2) is token-free (directional probe).

    .venv-tabpfn25/bin/python experiments/tabpfn_prior.py --leakage-check
    .venv-tabpfn25/bin/python experiments/tabpfn_prior.py --model v2 --design component_wise
    TABPFN_TOKEN=... .venv-tabpfn25/bin/python experiments/tabpfn_prior.py --model v2.5 --design component_wise

BAND LOGIC (single source of truth for n; owner decision 2026-07-14)
--------------------------------------------------------------------
n := n_author = same-author analog count as-of the fold (== the honest variant's
recorded n_author; verified identical to an independent as-of replay). Bands
partition every fold for any threshold t:

    Band A  (n_a == 0 AND n_g == 0)  -> global terminus; SKIP TabPFN, use honest WA.
    Band C  (n_a >  t)               -> sufficient author support; honest WA unchanged.
    Band B  (otherwise)              -> TabPFN prior (cold slice). Guards: on error/
                                        non-finite -> honest WA (fallback, logged);
                                        final WA outside the past-pool WA range ->
                                        clamp (logged).

Headline threshold t=0 (Band B == the genre-only slice, honest MAE 0.746 to beat).
Sweep t in {0,1,2,3,5} widens Band B up the author-support ladder.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

# Parked in experiments/; add the repo root so the read-only engine modules import
# whether this is run from root or from experiments/ (see experiments/README.md).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db_loader
import research_predict as rp
import reresearch_and_measure as rm
import walkforward as wf

LIVE = rm.LIVE                                   # canonical 14 components, ref order
WB = set(rm.WB)                                  # the 3 worldbuilding comps (0.0 sentinel)
FOLDS_PATH = os.path.join(ROOT, "validation", "walkforward_folds.jsonl")
XLSX = os.path.join(ROOT, "BookRankingsNew.xlsx")
SWEEP = (0, 1, 2, 3, 5)                           # author-support thresholds for Band B
SEED = 0

# Model-version selection. "v2.5" is the brief's target (needs TABPFN_TOKEN); "v2"
# is the token-free directional proxy. Resolved to the tabpfn ModelVersion enum
# lazily in _make_regressor so this module imports without torch/tabpfn present.
_MODEL_ENUM = {"v2": "V2", "v2.5": "V2_5", "v2.6": "V2_6", "v3": "V3"}


# ---------------------------------------------------------------------------
# Inputs: champion folds + feature/target table (all past-only-safe)
# ---------------------------------------------------------------------------
def load_baseline_folds(path=FOLDS_PATH):
    """Read the committed walk-forward folds -> the evaluated (non-skip) records,
    sorted by reading position. Each carries the champion honest WA, actual WA, and
    the as-of n_author / n_genre we band on — the exact 0.636 baseline, verbatim."""
    folds = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not rec.get("skip"):
                folds.append(rec)
    folds.sort(key=lambda f: f["position"])
    return folds


def _genre_code_map(books):
    """Deterministic genre -> int label encoding (config-like; encodes no outcome,
    so it is not leakage). Built from the full genre set for a stable mapping."""
    genres = sorted(set(str(g) for g in books["Genre"].dropna().unique()))
    return {g: i for i, g in enumerate(genres)}


def build_feature_target_table(books, cache, gmap):
    """title -> {vec, wa, comps, wb_sentinel} for every book with a FULL 14-component
    cache vector.
      vec          = 14 raw richer-prompt LLM components + genre code (features).
      wa           = the reader's actual WA (target for wa_direct).
      comps        = the reader's 14 actual component scores (targets for
                     component_wise); None where missing.
      wb_sentinel  = True if all three WB actuals are exactly 0 (realist-genre
                     sentinel), matching reresearch_and_measure's WB exclusion."""
    wa_by = dict(zip(books["Book"], books["WA"]))
    genre_by = dict(zip(books["Book"], books["Genre"]))
    comp_by = {c: dict(zip(books["Book"], books[c])) for c in LIVE}
    out = {}
    for title, entry in cache.items():
        if title not in wa_by:
            continue
        scores = entry.get("scores") if isinstance(entry, dict) else None
        if not isinstance(scores, dict) or any(c not in scores for c in LIVE):
            continue
        try:
            vec = [float(scores[c]) for c in LIVE]
        except (TypeError, ValueError):
            continue
        gcode = float(gmap.get(str(genre_by.get(title)), -1))
        comps = {}
        for c in LIVE:
            v = comp_by[c].get(title)
            comps[c] = (None if v is None or (isinstance(v, float) and np.isnan(v))
                        else float(v))
        wb_sentinel = all(comps.get(w) == 0.0 for w in WB)
        out[title] = {"vec": np.array(vec + [gcode], dtype=float),
                      "wa": float(wa_by[title]), "comps": comps,
                      "wb_sentinel": wb_sentinel}
    return out


def query_vector(title, cache, gmap, genre):
    """The target book's own feature vector (same layout as the support table)."""
    scores = cache[title]["scores"]
    vec = [float(scores[c]) for c in LIVE]
    return np.array(vec + [float(gmap.get(str(genre), -1))], dtype=float)


# ---------------------------------------------------------------------------
# Band assignment (single source of truth; identical in validate + serve)
# ---------------------------------------------------------------------------
def band_of(n_author, n_genre, threshold):
    """A / B / C per the locked design. Partitions every fold for any threshold."""
    if n_author == 0 and n_genre == 0:
        return "A"
    if n_author > threshold:
        return "C"
    return "B"


# ---------------------------------------------------------------------------
# TabPFN regressor (lazy import; isolated venv). One instance, refit per call.
# ---------------------------------------------------------------------------
def _make_regressor(model="v2.5"):
    from tabpfn import TabPFNRegressor
    from tabpfn.constants import ModelVersion
    mv = getattr(ModelVersion, _MODEL_ENUM[model])
    return TabPFNRegressor.create_default_for_version(mv, device="cpu", random_state=SEED)


def _fit_predict(reg, X, y, xq):
    reg.fit(X, y)
    return float(reg.predict(xq.reshape(1, -1))[0])


# ---------------------------------------------------------------------------
# The two challenger designs -> a predicted WA for one target book
# ---------------------------------------------------------------------------
def predict_wa_direct(reg, feat, sup_titles, xq):
    """TabPFN predicts WA directly. Returns (wa, (pool_wa_lo, pool_wa_hi))."""
    X = np.array([feat[t]["vec"] for t in sup_titles])
    y = np.array([feat[t]["wa"] for t in sup_titles])
    wa = _fit_predict(reg, X, y, xq)
    return wa, (float(y.min()), float(y.max()))


def predict_component_wise(reg, feat, sup_titles, xq, cache, target_title, genre, gw, gcw):
    """TabPFN predicts each actual component (LLM->actual), then the engine's exact
    _wa_from_components rolls up with the target's genre weights. WB components drop
    the realist-genre sentinel rows from their pool. Returns (wa, pool_wa_range)."""
    corrected = {}
    for c in LIVE:
        pool = ([t for t in sup_titles if not feat[t]["wb_sentinel"]]
                if c in WB else sup_titles)
        rows = [(feat[t]["vec"], feat[t]["comps"][c]) for t in pool
                if feat[t]["comps"][c] is not None]
        if len(rows) < 2:
            # Too thin to train this component -> keep the raw LLM value for it.
            corrected[c] = float(cache[target_title]["scores"][c])
            continue
        X = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        ch = _fit_predict(reg, X, y, xq)
        corrected[c] = float(min(max(ch, 0.0), 10.0))     # component valid range
    wa = rp._wa_from_components(corrected, genre, gw, gcw)
    yw = np.array([feat[t]["wa"] for t in sup_titles])
    return wa, (float(yw.min()), float(yw.max()))


# ---------------------------------------------------------------------------
# Core: compute the TabPFN prior for every Band-B-eligible fold (once), with the
# leakage guard, error fallback, and range clamp. Threshold-independent, so the
# sweep just re-buckets these results.
# ---------------------------------------------------------------------------
def compute_challenger(folds, feat, pos_by_title, cache, gmap, gw, gcw, reg,
                       design="component_wise", max_threshold=max(SWEEP), verbose=True):
    """title -> record with the challenger WA, the guards that fired, and the champion
    honest WA / actual WA / n_a / n_g needed for banding + scoring."""
    def predict_one(sup_titles, xq, title, genre):
        if design == "wa_direct":
            return predict_wa_direct(reg, feat, sup_titles, xq)
        return predict_component_wise(reg, feat, sup_titles, xq, cache, title, genre, gw, gcw)

    superset = [f for f in folds
                if not (f["variants"]["honest"]["n_author"] == 0
                        and f["variants"]["honest"]["n_genre"] == 0)
                and f["variants"]["honest"]["n_author"] <= max_threshold]
    if verbose:
        print(f"[{design}] TabPFN prior over {len(superset)} Band-B-eligible folds "
              f"(n_author<= {max_threshold}, excluding the global terminus)...")

    results, t0, done = {}, time.time(), 0
    for f in folds:
        h = f["variants"]["honest"]
        n_a, n_g = h["n_author"], h["n_genre"]
        title, pos, genre = f["title"], f["position"], f["genre"]
        rec = {"title": title, "position": pos, "genre": genre,
               "n_author": n_a, "n_genre": n_g,
               "honest_wa": float(h["wa"]), "actual_wa": float(f["actual_wa"]),
               "tab_wa": None, "clamped": False, "fallback": False}
        eligible = not (n_a == 0 and n_g == 0) and n_a <= max_threshold
        if eligible:
            sup_titles = sorted(t for t, p in pos_by_title.items()
                                if p < pos and t in feat)
            for t in sup_titles:                       # leakage guard
                assert pos_by_title[t] < pos, f"LEAKAGE: {t} pos>= {title}"
            try:
                xq = query_vector(title, cache, gmap, genre)
                wa, (lo, hi) = predict_one(sup_titles, xq, title, genre)
                if not np.isfinite(wa):
                    raise ValueError("non-finite TabPFN output")
                if wa < lo or wa > hi:
                    rec["clamped"] = True
                    wa = min(max(wa, lo), hi)
                rec["tab_wa"] = float(wa)
            except Exception as e:                     # never fail a prediction
                rec["fallback"] = True
                rec["tab_wa"] = rec["honest_wa"]
                if verbose:
                    print(f"  fallback @pos {pos} {title[:30]}: {type(e).__name__}: {e}")
            done += 1
            if verbose and done % 10 == 0:
                print(f"  ...{done}/{len(superset)} predicted "
                      f"({time.time()-t0:.0f}s elapsed)")
        results[title] = rec
    if verbose:
        print(f"  done: {done} predictions in {time.time()-t0:.0f}s")
    return results


def challenged_wa(rec, threshold):
    """The WA the challenger serves for this fold: honest on Band A/C, the guarded
    TabPFN WA on Band B."""
    if band_of(rec["n_author"], rec["n_genre"], threshold) == "B":
        return rec["tab_wa"]
    return rec["honest_wa"]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _mae(pairs):
    return float(np.mean([abs(p - a) for p, a in pairs])) if pairs else float("nan")


def report(results, model, design):
    print("\n" + "=" * 74)
    print(f"TABPFN COLD-START PRIOR — challenger  (model={model}, design={design})")
    print("=" * 74)
    honest_overall = _mae([(r["honest_wa"], r["actual_wa"]) for r in results.values()])
    print(f"Champion honest overall WA MAE (all {len(results)} folds): {honest_overall:.4f}"
          f"   [committed baseline 0.636]\n")

    header = (f"{'thr':>3} | {'BandB n':>7} {'base MAE':>8} {'TFM MAE':>8} {'Δ':>7} "
              f"{'fb%':>5} {'clamp%':>6} {'TFM(no-fb)':>10} | "
              f"{'overall':>7} {'Δover':>7} | {'BandC MAE':>9}")
    print(header)
    print("-" * len(header))

    summary = {}
    for thr in SWEEP:
        bandB = [r for r in results.values()
                 if band_of(r["n_author"], r["n_genre"], thr) == "B"]
        bandC = [r for r in results.values()
                 if band_of(r["n_author"], r["n_genre"], thr) == "C"]
        base_B = _mae([(r["honest_wa"], r["actual_wa"]) for r in bandB])
        tfm_B = _mae([(challenged_wa(r, thr), r["actual_wa"]) for r in bandB])
        n_fb = sum(1 for r in bandB if r["fallback"])
        n_cl = sum(1 for r in bandB if r["clamped"])
        fb_rate = n_fb / len(bandB) if bandB else 0.0
        cl_rate = n_cl / len(bandB) if bandB else 0.0
        bandB_nofb = [r for r in bandB if not r["fallback"]]
        tfm_B_nofb = _mae([(challenged_wa(r, thr), r["actual_wa"]) for r in bandB_nofb])
        base_B_nofb = _mae([(r["honest_wa"], r["actual_wa"]) for r in bandB_nofb])
        overall = _mae([(challenged_wa(r, thr), r["actual_wa"]) for r in results.values()])
        delta_over = overall - honest_overall
        baseC = _mae([(r["honest_wa"], r["actual_wa"]) for r in bandC])
        print(f"{thr:>3} | {len(bandB):>7} {base_B:>8.4f} {tfm_B:>8.4f} {tfm_B-base_B:>+7.4f} "
              f"{fb_rate*100:>4.0f}% {cl_rate*100:>5.0f}% {tfm_B_nofb:>10.4f} | "
              f"{overall:>7.4f} {delta_over:>+7.4f} | {baseC:>9.4f}")
        summary[thr] = {"bandB_n": len(bandB), "base_B": base_B, "tfm_B": tfm_B,
                        "delta_B": tfm_B - base_B, "fb_rate": fb_rate, "clamp_rate": cl_rate,
                        "tfm_B_nofb": tfm_B_nofb, "base_B_nofb": base_B_nofb,
                        "overall": overall, "delta_overall": delta_over, "baseC": baseC}

    print("\nLegend: base MAE = champion honest on Band B (the number to beat); "
          "TFM MAE = challenger on Band B;\n  Δ = TFM−base (negative = TabPFN helps); "
          "fb% = fallback rate; TFM(no-fb) = Band-B MAE excluding fallbacks;\n"
          "  overall = full-fold MAE (only Band B changes); BandC MAE unchanged (=honest).")

    s0 = summary[0]
    best_thr = min(SWEEP, key=lambda t: summary[t]["tfm_B"])
    print("\n" + "-" * 74 + "\nGO / NO-GO")
    print(f"  headline t=0 (genre-only): base {s0['base_B']:.4f} -> TFM {s0['tfm_B']:.4f} "
          f"(Δ {s0['delta_B']:+.4f}), overall {s0['overall']:.4f} (Δ {s0['delta_overall']:+.4f})")
    b = summary[best_thr]
    print(f"  best sweep point t={best_thr}: base {b['base_B']:.4f} -> TFM {b['tfm_B']:.4f} "
          f"(Δ {b['delta_B']:+.4f}), overall {b['overall']:.4f} (Δ {b['delta_overall']:+.4f})")
    win = any(summary[t]["tfm_B"] < summary[t]["base_B"] and summary[t]["delta_overall"] <= 1e-9
              for t in SWEEP)
    print(f"  VERDICT: {'SIGNAL — a threshold beats Band-B baseline without degrading overall' if win else 'NO-GO — no threshold beats the cold-slice baseline without hurting overall'}")
    print("  (For comparison, the TabPFN-v2 bake-off lost overall by +0.067.)")
    return summary


# ---------------------------------------------------------------------------
# Leakage-only self-check (no TabPFN import needed)
# ---------------------------------------------------------------------------
def leakage_check(folds, feat, pos_by_title):
    checked = 0
    for f in folds:
        h = f["variants"]["honest"]
        if (h["n_author"] == 0 and h["n_genre"] == 0) or h["n_author"] > max(SWEEP):
            continue
        pos = f["position"]
        sup = [t for t, p in pos_by_title.items() if p < pos and t in feat]
        for t in sup:
            assert pos_by_title[t] < pos, f"LEAKAGE at {f['title']}: {t}"
        assert f["title"] not in sup, f"target {f['title']} in its own support"
        checked += 1
    print(f"LEAKAGE CHECK: PASS — {checked} Band-B folds, every support book strictly "
          f"precedes its target in reading order (walkforward.build_order).")


def _determinism_check(reg, feat, folds, pos_by_title, cache, gmap, gw, gcw, design):
    """Fit+predict the first eligible fold twice (with an intervening different fit)
    to confirm the reused regressor refits deterministically."""
    for f in folds:
        h = f["variants"]["honest"]
        if (h["n_author"] == 0 and h["n_genre"] == 0) or h["n_author"] > max(SWEEP):
            continue
        pos, title, genre = f["position"], f["title"], f["genre"]
        sup = sorted(t for t, p in pos_by_title.items() if p < pos and t in feat)
        xq = query_vector(title, cache, gmap, genre)
        pred = (lambda: (predict_wa_direct(reg, feat, sup, xq) if design == "wa_direct"
                         else predict_component_wise(reg, feat, sup, xq, cache, title, genre, gw, gcw)))
        a = pred()[0]
        _fit_predict(reg, np.array([feat[t]["vec"] for t in sup[:8]]),
                     np.array([feat[t]["wa"] for t in sup[:8]]), xq)   # perturb
        b = pred()[0]
        print(f"DETERMINISM: refit reproducible = {abs(a-b) < 1e-9} (|Δ|={abs(a-b):.2e})")
        return


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _has_tabpfn_token():
    """A license token is available if TABPFN_TOKEN is set OR the interactive flow
    already cached one (~/.cache/tabpfn/auth_token or ~/.tabpfn/token)."""
    if os.environ.get("TABPFN_TOKEN"):
        return True
    from pathlib import Path
    return any((Path.home() / p).is_file() and (Path.home() / p).stat().st_size > 0
               for p in (".cache/tabpfn/auth_token", ".tabpfn/token"))


def main():
    ap = argparse.ArgumentParser(description="TabPFN cold-start prior challenger (offline).")
    ap.add_argument("--model", default=os.environ.get("TABPFN_MODEL", "v2.5"),
                    choices=list(_MODEL_ENUM), help="TabPFN model generation to pin.")
    ap.add_argument("--design", default="component_wise",
                    choices=("wa_direct", "component_wise"), help="challenger design.")
    ap.add_argument("--leakage-check", action="store_true",
                    help="run only the leakage sanity check (no TabPFN import).")
    ap.add_argument("--out", default=None, help="optional JSON path for the summary.")
    ap.add_argument("--dump", default=None,
                    help="optional JSON path for the per-fold challenger records "
                         "(title/genre/n_author/n_genre/honest_wa/tab_wa/actual_wa/guards).")
    args = ap.parse_args()

    books, gw, gcw = db_loader.load_from_db()
    cache = rp.load_cache()
    order, _ = wf.build_order(books, XLSX)
    pos_by_title = {e["title"]: e["position"] for e in order}
    gmap = _genre_code_map(books)
    feat = build_feature_target_table(books, cache, gmap)
    folds = load_baseline_folds()
    print(f"Loaded {len(folds)} evaluated folds, {len(feat)} cache-backed books, "
          f"{len(order)} ordered titles.")

    if args.leakage_check:
        leakage_check(folds, feat, pos_by_title)
        return

    if args.model in ("v2.5", "v2.6", "v3") and not _has_tabpfn_token():
        raise SystemExit(
            f"model {args.model} needs a Prior Labs license token (one-time, free). "
            "Set TABPFN_TOKEN=..., accept via the interactive browser flow (caches "
            "~/.cache/tabpfn/auth_token), or run --model v2 for the token-free probe.")

    leakage_check(folds, feat, pos_by_title)          # always gate before predicting
    reg = _make_regressor(args.model)
    _determinism_check(reg, feat, folds, pos_by_title, cache, gmap, gw, gcw, args.design)
    results = compute_challenger(folds, feat, pos_by_title, cache, gmap, gw, gcw, reg,
                                 design=args.design)
    summary = report(results, args.model, args.design)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"model": args.model, "design": args.design, "summary": summary},
                      fh, indent=2, sort_keys=True)
        print(f"\nwrote {args.out}")

    if args.dump:
        with open(args.dump, "w") as fh:
            json.dump(sorted(results.values(), key=lambda r: r["position"]),
                      fh, indent=2, sort_keys=True)
        print(f"wrote per-fold dump {args.dump}")


if __name__ == "__main__":
    main()
