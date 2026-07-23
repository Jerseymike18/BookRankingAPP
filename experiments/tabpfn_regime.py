"""
experiments/tabpfn_regime.py  —  OFFLINE ONLY.  Cold-start regime slice runner.
================================================================================
Brief 1 ("Cold-start regime slice: TabPFN vs. engine by support-set size").

WHAT THIS IS
------------
A walk-forward runner that logs, for EVERY prediction point, the engine's honest
prediction alongside two TabPFN challengers and a naive baseline, tagged with
N = the number of the user's books rated STRICTLY BEFORE the target at prediction
time (== the harness pool_size == walk-forward position - 1). Phase 2 then buckets
the per-prediction errors by N to locate any low-N regime where TabPFN wins.

It DUPLICATES walkforward.py's logic (the brief sanctions "wraps/duplicates ...
don't edit it destructively") and REUSES the read-only engine + the committed
bake-off modules verbatim:
  * order + zero-API guard : walkforward.build_order / _install_no_api_guard
  * engine honest WA       : research_predict.correct_and_predict  (the EXACT call
                             walkforward._variant_corrected makes for `honest`)
  * TabPFN wrapper         : challenger_tabpfn.TabPFNChallenger  (v2, Apache-2.0,
                             token-free, seeded/deterministic, CPU)
  * causal author/genre priors : challenger_features.build_row / causal_feature_rows

TWO CHALLENGERS (owner decision 2026-07-22: "run both")
-------------------------------------------------------
  mirror   The apples-to-apples "same information the engine had" challenger.
           X = 14 raw richer-prompt LLM components + genre code + causal
           author/genre prior mean & count (the quantities correct_book conditions
           on). NO word-count / series (the honest engine baseline cannot see
           them). TabPFN predicts WA directly from the past-only pool.
  meta     The prior bake-off challenger, VERBATIM: challenger_features' 8 causal
           metadata features (author/genre prior mean/count/std, word_count,
           series flag/position) — no LLM vector. Reproduced here on CURRENT data
           so its champion aligns with today's engine.

CHAMPION (engine honest) — computed inline, validated against walkforward.py
----------------------------------------------------------------------------
The honest WA is computed by calling correct_and_predict exactly as
walkforward._variant_corrected does (same pool, same corr_models, same args).
`--validate-champion <folds.jsonl>` asserts this inline WA reproduces the
authoritative walkforward run fold-for-fold (the "walkforward.py is the authority"
gate). resid_sd feeds only the CI (never the WA point), so a thin-pool regression
that underdetermines it cannot move the champion — which is what lets the
exploratory low-burn-in run reach genuinely-cold N without walkforward's report
regression choking.

HARD BOUNDARIES (see CLAUDE.md)
-------------------------------
  * OFFLINE ONLY. Never imported by predict_engine / backend / the serve path.
  * NO WRITES to books.db / db_write. Artifacts are CSV + JSONL under the out-dir.
  * Read-only engine: reimplements NO prediction math; the champion WA is the
    engine's own correct_and_predict output.
  * DeltaTracker / component_corrections untouched (retired-to-zero; enters nothing).

RUNTIME
-------
    .venv-tabpfn/bin/python experiments/tabpfn_regime.py --burn-in 15 \
        --validate-champion validation/regime/bi15/walkforward_folds.jsonl
    .venv-tabpfn/bin/python experiments/tabpfn_regime.py --burn-in 3
    .venv-tabpfn/bin/python experiments/tabpfn_regime.py --burn-in 15 --check-determinism
"""

import argparse
import hashlib
import json
import os
import sys
import csv

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db_backend
import db_loader
import predict_engine as pe
import research_predict as rp
import reresearch_and_measure as rm
import walkforward as wf
import challenger_features as cf
import challenger_tabpfn as ct

LIVE = rm.LIVE                                   # canonical 14 components, ref order
WB = set(rm.WB)                                  # the 3 worldbuilding comps (0.0 sentinel)
XLSX = os.path.join(ROOT, "BookRankingsNew.xlsx")
OUT_DIR = os.path.join(ROOT, "validation", "regime")

# mirror feature column order (frozen for reproducibility).
MIRROR_FEATURES = (["llm_" + c for c in LIVE]
                   + ["genre_code", "author_prior_mean", "author_prior_count",
                      "genre_prior_mean", "genre_prior_count"])


def _r(x):
    """Fixed-precision float for byte-identical serialisation (mirrors walkforward._r)."""
    if x is None:
        return None
    if isinstance(x, (int, np.integer)) and not isinstance(x, bool):
        return int(x)
    return round(float(x), 6)


# ---------------------------------------------------------------------------
# Inputs: books + cache + read order (all past-only-safe, via the harness)
# ---------------------------------------------------------------------------
def load_inputs():
    wf._install_no_api_guard()                   # structural zero-spend guard
    books, gw, gcw = db_loader.load_from_db()
    cache = rp.load_cache()
    order, _ = wf.build_order(books, XLSX)
    order.sort(key=lambda e: e["position"])
    return books, gw, gcw, cache, order


def _genre_code_map(books):
    """Deterministic genre -> int (config-like; encodes no outcome). Full genre set."""
    genres = sorted(set(str(g) for g in books["Genre"].dropna().unique()))
    return {g: i for i, g in enumerate(genres)}


def build_ordered(books, order):
    """One record per rated fiction book in walk-forward read order, carrying the
    metadata both challengers need + the actual WA (the shared target)."""
    wa_by = dict(zip(books["Book"], books["WA"]))
    words_by = dict(zip(books["Book"], books["Words"]))
    ordered = []
    for e in order:
        t = e["title"]
        w = words_by.get(t)
        ordered.append({
            "position": e["position"], "title": t,
            "author": e["author"], "genre": e["genre"],
            "series": e["series"], "series_number": e["series_number"],
            "words": (None if w is None or (isinstance(w, float) and np.isnan(w))
                      else int(w)),
            "wa": float(wa_by[t]),
        })
    return ordered


# ---------------------------------------------------------------------------
# Champion: engine honest WA, computed EXACTLY as walkforward._variant_corrected
# ---------------------------------------------------------------------------
def champion_honest_wa(books, pool_titles, title, author, genre, raw_scores, conf,
                       gw, gcw, cache):
    books_pool = books[books["Book"].isin(pool_titles)]
    try:
        resid_sd = pe.fit_regression(books_pool)[2]      # CI-only; never moves WA
    except Exception:
        resid_sd = 0.0
    corr_models = rp.build_corr_models(books_pool, cache)
    res = rp.correct_and_predict(
        title, author, genre, dict(raw_scores), conf, resid_sd,
        books_pool, gw, gcw, cache, corr_models=corr_models)
    return float(res["wa"]), int(res["n_author"]), int(res["n_genre"])


# ---------------------------------------------------------------------------
# Feature matrices for the two TabPFN challengers (both causal / past-only)
# ---------------------------------------------------------------------------
def mirror_matrix(ordered, cache, gmap):
    """(N, 19) mirror features. Row i's author/genre priors come from ordered[:i]
    (causal, via challenger_features.build_row); its 14 LLM comps + genre code are
    the book's own known-before-reading research vector. NaN encodes a missing
    prior (TabPFN-native), exactly as the metadata challenger does."""
    m = np.full((len(ordered), len(MIRROR_FEATURES)), np.nan, dtype=float)
    for i, target in enumerate(ordered):
        scores = cache[target["title"]]["scores"]
        row = cf.build_row(target, ordered[:i])          # reused causal priors
        vals = {}
        for c in LIVE:
            vals["llm_" + c] = float(scores[c])
        vals["genre_code"] = float(gmap.get(str(target["genre"]), -1))
        vals["author_prior_mean"] = row["author_prior_mean"]
        vals["author_prior_count"] = row["author_prior_count"]
        vals["genre_prior_mean"] = row["genre_prior_mean"]
        vals["genre_prior_count"] = row["genre_prior_count"]
        for j, name in enumerate(MIRROR_FEATURES):
            v = vals.get(name)
            if v is not None:
                m[i, j] = float(v)
    return m


def metadata_matrix(ordered):
    """(N, 8) bake-off metadata features, verbatim challenger_features."""
    feat_rows = [row for _rec, row in cf.causal_feature_rows(ordered)]
    return cf.to_matrix(feat_rows)


# ---------------------------------------------------------------------------
# Core walk-forward run
# ---------------------------------------------------------------------------
def run(burn_in, limit=None, seed=ct.SEED):
    books, gw, gcw, cache, order = load_inputs()
    ordered = build_ordered(books, order)
    gmap = _genre_code_map(books)
    N = len(ordered)

    # Every book must carry a full 14-component cache vector (else it can neither
    # be a mirror query nor a context row). The current library satisfies this
    # (walk-forward meta: 0 SKIPPED_NO_CACHE); assert rather than silently drop.
    for rec in ordered:
        sc = cache.get(rec["title"], {}).get("scores")
        assert isinstance(sc, dict) and all(c in sc for c in LIVE), \
            f"missing cache vector for {rec['title']!r}"

    y_all = np.array([rec["wa"] for rec in ordered], dtype=float)
    eval_indices = [i for i in range(N) if i >= burn_in]     # pos = i+1 > burn_in
    if limit:
        eval_indices = eval_indices[:limit]

    # TabPFN challengers (v2, seeded, deterministic). Separate instances so the
    # 8-dim and 19-dim feature spaces never share a fitted state.
    Xmeta = metadata_matrix(ordered)
    Xmirror = mirror_matrix(ordered, cache, gmap)
    meta_preds = ct.TabPFNChallenger(seed=seed).run_walkforward(Xmeta, y_all, eval_indices)
    mirror_preds = ct.TabPFNChallenger(seed=seed).run_walkforward(Xmirror, y_all, eval_indices)

    tenant = db_backend.DEFAULT_USER_ID
    rows = []
    for i in eval_indices:
        rec = ordered[i]
        title, author, genre = rec["title"], rec["author"], rec["genre"]
        actual = rec["wa"]
        pool_titles = [ordered[j]["title"] for j in range(i)]

        raw_scores = {c: float(cache[title]["scores"][c]) for c in LIVE}
        conf = cache[title].get("conf", "?")
        champ_wa, n_author, n_genre = champion_honest_wa(
            books, pool_titles, title, author, genre, raw_scores, conf, gw, gcw, cache)

        mirror_wa = float(mirror_preds[i]["pred"])
        meta_wa = float(meta_preds[i]["pred"])
        naive_wa = float(np.mean(y_all[:i]))     # past-pool mean WA (walk-forward naive)

        rows.append({
            "tenant_id": tenant,
            "position": rec["position"],
            "N": i,                               # == pool_size == position - 1
            "title": title, "genre": genre, "author": author,
            "n_author": n_author, "n_genre": n_genre,
            "actual_wa": _r(actual), "weight": 1.0,
            "champion_wa": _r(champ_wa), "champion_abs": _r(abs(champ_wa - actual)),
            "champion_signed": _r(champ_wa - actual),
            "mirror_wa": _r(mirror_wa), "mirror_abs": _r(abs(mirror_wa - actual)),
            "mirror_signed": _r(mirror_wa - actual),
            "meta_wa": _r(meta_wa), "meta_abs": _r(abs(meta_wa - actual)),
            "meta_signed": _r(meta_wa - actual),
            "naive_wa": _r(naive_wa), "naive_abs": _r(abs(naive_wa - actual)),
        })
    rows.sort(key=lambda r: r["position"])
    return rows


# ---------------------------------------------------------------------------
# Serialisation + validation
# ---------------------------------------------------------------------------
CSV_COLS = ["tenant_id", "position", "N", "title", "genre", "author",
            "n_author", "n_genre", "actual_wa", "weight",
            "champion_wa", "champion_abs", "champion_signed",
            "mirror_wa", "mirror_abs", "mirror_signed",
            "meta_wa", "meta_abs", "meta_signed",
            "naive_wa", "naive_abs"]


def _serialise_jsonl(rows):
    return "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"


def write_artifacts(rows, out_dir, burn_in):
    os.makedirs(out_dir, exist_ok=True)
    stem = f"perpred_bi{burn_in:02d}"
    with open(os.path.join(out_dir, stem + ".jsonl"), "w") as fh:
        fh.write(_serialise_jsonl(rows))
    with open(os.path.join(out_dir, stem + ".csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return stem


def validate_champion(rows, folds_path):
    """Assert the inline champion WA reproduces the authoritative walkforward run
    fold-for-fold (the 'walkforward.py is the authority' gate)."""
    fold_wa = {}
    with open(folds_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not rec.get("skip"):
                fold_wa[rec["title"]] = rec["variants"]["honest"]["wa"]
    checked, worst = 0, 0.0
    for r in rows:
        if r["title"] in fold_wa:
            d = abs(r["champion_wa"] - fold_wa[r["title"]])
            worst = max(worst, d)
            checked += 1
    ok = worst < 1e-6
    print(f"CHAMPION VALIDATION: {'PASS' if ok else 'FAIL'} — {checked} folds "
          f"cross-checked vs {os.path.basename(folds_path)}, worst |Δ|={worst:.2e}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Cold-start regime slice runner (offline).")
    ap.add_argument("--burn-in", type=int, default=15)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--limit", type=int, default=None,
                    help="evaluate only the first K folds (smoke test).")
    ap.add_argument("--validate-champion", default=None,
                    help="path to an authoritative walkforward_folds.jsonl to cross-check.")
    ap.add_argument("--check-determinism", action="store_true",
                    help="run twice, assert byte-identical output.")
    args = ap.parse_args()

    if args.check_determinism:
        a = _serialise_jsonl(run(args.burn_in, limit=args.limit))
        b = _serialise_jsonl(run(args.burn_in, limit=args.limit))
        ha, hb = (hashlib.sha256(x.encode()).hexdigest() for x in (a, b))
        print(f"run A sha256: {ha}")
        print(f"run B sha256: {hb}")
        print("DETERMINISM: PASS" if a == b else "DETERMINISM: FAIL")
        raise SystemExit(0 if a == b else 1)

    rows = run(args.burn_in, limit=args.limit)
    stem = write_artifacts(rows, args.out_dir, args.burn_in)
    print(f"regime run: burn-in {args.burn_in}, {len(rows)} folds -> {stem}.csv / .jsonl")

    # Quick console summary (unweighted WA MAE, matching the harness yardstick).
    def mae(key):
        return float(np.mean([r[key] for r in rows]))
    print(f"  overall WA MAE  champion {mae('champion_abs'):.4f} | "
          f"mirror {mae('mirror_abs'):.4f} | meta {mae('meta_abs'):.4f} | "
          f"naive {mae('naive_abs'):.4f}")
    if args.validate_champion:
        validate_champion(rows, args.validate_champion)


if __name__ == "__main__":
    main()
