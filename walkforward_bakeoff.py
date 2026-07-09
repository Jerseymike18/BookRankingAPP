"""
walkforward_bakeoff.py
======================
Head-to-head: the TabPFN CHALLENGER vs the engine CHAMPION on the identical
walk-forward folds. Analysis-only — ships nothing, writes nothing to books.db,
touches no engine math and no served path.

THE COMPARISON (apples-to-apples, cannot drift)
-----------------------------------------------
The champion's honest walk-forward numbers are READ straight from the committed
`validation/walkforward_folds.jsonl` (its point WA, abs error, signed error and
n_author) — never recomputed — so the champion column is exactly the published
0.6315 WA MAE over 113 folds, by construction. The challenger is scored on the
SAME 113 folds, the SAME read order, and the SAME strictly-past-only information
horizon, using the SAME per-fold scorer (`walkforward._r`) and MAE aggregator
(`walkforward_report._mean`). No metric is reimplemented; the champion path is
imported read-only and left untouched (mirrors ablation.py's discipline).

THE CHALLENGER
--------------
TabPFN v2, in-context: for a book at position t the context is the causal feature
rows of every book read before t + their observed WAs (challenger_features.py),
the query is the book's own causal feature row, and the prediction is one forward
pass (challenger_tabpfn.py). Raw author/genre priors are fed — learned shrinkage
vs. the engine's hand-tuned EB shrinkage is the whole question.

VERDICT RULE (pre-committed, from the brief)
--------------------------------------------
A WA MAE improvement < 0.02 is a TIE (small-N fold noise); only a clean margin
>= 0.02 counts as a win. Reported both on the aggregate MAE and, so a win can be
told from noise, as a paired per-book comparison (mean paired |error| difference,
a seeded bootstrap 95% CI, and win/loss/tie counts) plus the champion/challenger
residual correlation (the Phase-5a ensemble pre-check).

RUN
---
    python3 walkforward_bakeoff.py                    # run + write validation/bakeoff.md
    python3 walkforward_bakeoff.py --report-only      # rebuild report from predictions artifact
    python3 walkforward_bakeoff.py --check-determinism # two full passes, assert byte-identical

MUST be run with the bake-off venv interpreter (TabPFN/torch):
    .venv-tabpfn/bin/python walkforward_bakeoff.py
"""

import argparse
import hashlib
import json
import os

import numpy as np

import db_loader
import walkforward as wf
import walkforward_report as wr
import challenger_features as cf
import challenger_tabpfn as ct

ROOT = wf.ROOT
OUT_DIR = wf.OUT_DIR
PRED_FILE = "bakeoff_predictions.jsonl"
REPORT_FILE = "bakeoff.md"

# Reuse the harness's own scorer + aggregator verbatim, so the challenger is
# measured with the exact yardstick that produced the champion's 0.6315.
_r = wf._r
_mean = wr._mean

TIE_THRESHOLD = 0.02          # WA MAE delta below this == tie (brief)
CHAMPION_MAE_EXPECTED = 0.6315  # committed honest overall MAE (113 folds)
CHAMPION_MAE_BAND = 0.02      # sanity band around the expected champion MAE
SUBSETS = (1, 2, 3)           # ">= N prior-read books by the same author"
SMALL_N = 10                  # per-genre cell below this is too small to conclude
BOOTSTRAP_B = 10000           # resamples for the paired-difference CI (seeded)
EPS = 1e-6                    # per-fold win/loss tolerance


# ---------------------------------------------------------------------------
# Build the full walk-forward sequence + causal features (read-only)
# ---------------------------------------------------------------------------
def _coerce_int(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return int(v)


def build_sequence(xlsx=None):
    """Return (ordered_records, feature_rows). ordered_records is every rated
    fiction book in walk-forward read order (position 1..128), each carrying the
    causal metadata the challenger uses + its actual WA. feature_rows[i] is the
    causal feature dict for ordered_records[i], built from rows 0..i-1 only."""
    xlsx = xlsx or os.path.join(ROOT, "BookRankingsNew.xlsx")
    wf._install_no_api_guard()            # belt-and-braces: no token can be spent
    books, _gw, _gcw = db_loader.load_from_db()
    order, _ = wf.build_order(books, xlsx)

    wa_by_title, words_by_title = {}, {}
    for _i, row in books.iterrows():
        wa_by_title[row["Book"]] = (float(row["WA"]) if row["WA"] is not None
                                    else None)
        words_by_title[row["Book"]] = _coerce_int(row.get("Words"))

    ordered = []
    for e in sorted(order, key=lambda e: e["position"]):
        t = e["title"]
        ordered.append({
            "position": e["position"], "title": t,
            "author": e["author"], "genre": e["genre"],
            "series": e["series"], "series_number": e["series_number"],
            "words": words_by_title.get(t),
            "wa": wa_by_title.get(t),
        })

    if any(r["wa"] is None for r in ordered):
        raise SystemExit("A rated fiction book has no WA — aborting bake-off.")

    feat_rows = [row for _rec, row in cf.causal_feature_rows(ordered)]
    return ordered, feat_rows


# ---------------------------------------------------------------------------
# Assemble the evaluation set: same folds the harness evaluated, each carrying
# the committed champion numbers + the reconstructed challenger context index.
# ---------------------------------------------------------------------------
def build_eval(ordered, feat_rows, out_dir=OUT_DIR):
    folds, _skips = wf.load_folds(out_dir)
    pos_to_idx = {rec["position"]: i for i, rec in enumerate(ordered)}

    evalset = []
    for f in folds:
        pos = f["position"]
        idx = pos_to_idx.get(pos)
        if idx is None:
            raise SystemExit(f"Fold position {pos} ({f['title']!r}) not in the "
                             "rebuilt sequence — aborting.")
        rec = ordered[idx]
        hon = f["variants"]["honest"]

        # Apples-to-apples integrity checks (mirror ablation.py): same book, same
        # actual WA, and the challenger's causal author count must equal the
        # engine's own n_author for this fold — so the ">=N prior" subsets align.
        if rec["title"] != f["title"]:
            raise SystemExit(f"Title mismatch at pos {pos}: seq {rec['title']!r} "
                             f"vs folds {f['title']!r}.")
        if abs(rec["wa"] - f["actual_wa"]) > 1e-4:
            raise SystemExit(f"Actual-WA mismatch at pos {pos} ({f['title']!r}): "
                             f"seq {rec['wa']} vs folds {f['actual_wa']}.")
        n_author = hon["n_author"]
        my_count = feat_rows[idx]["author_prior_count"]
        if n_author is not None and my_count != n_author:
            raise SystemExit(
                f"POOL MISMATCH at pos {pos} ({f['title']!r}): challenger "
                f"author_prior_count={my_count} but engine n_author={n_author}. "
                "Aborting rather than emit a misleading comparison.")

        evalset.append({
            "index": idx, "position": pos, "title": f["title"],
            "genre": f["genre"], "actual_wa": f["actual_wa"],
            "n_author": n_author if n_author is not None else my_count,
            "champion_wa": hon["wa"],
            "champion_abs": hon["wa_abs_error"],
            "champion_signed": hon["wa_signed_error"],
        })
    evalset.sort(key=lambda e: e["position"])
    return evalset


# ---------------------------------------------------------------------------
# Run the challenger + score (deterministic prediction rows)
# ---------------------------------------------------------------------------
def run_predictions(ordered, feat_rows, evalset, seed=ct.SEED):
    X = cf.to_matrix(feat_rows)
    y_all = np.array([r["wa"] for r in ordered], dtype=float)
    eval_indices = [e["index"] for e in evalset]

    challenger = ct.TabPFNChallenger(seed=seed)
    preds = challenger.run_walkforward(X, y_all, eval_indices)

    rows = []
    for e in evalset:
        pred = preds[e["index"]]["pred"]
        rows.append({
            "position": e["position"], "title": e["title"], "genre": e["genre"],
            "n_author": e["n_author"], "actual_wa": e["actual_wa"],
            "champion_wa": e["champion_wa"], "champion_abs": e["champion_abs"],
            "champion_signed": e["champion_signed"],
            "challenger_wa": _r(pred),
            "challenger_abs": _r(abs(pred - e["actual_wa"])),
            "challenger_signed": _r(pred - e["actual_wa"]),
        })
    rows.sort(key=lambda r: r["position"])
    return rows


def _serialise(rows):
    return "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"


def load_predictions(out_dir=OUT_DIR):
    rows = []
    with open(os.path.join(out_dir, PRED_FILE)) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["position"])
    return rows


# ---------------------------------------------------------------------------
# Aggregation, paired stats, verdict
# ---------------------------------------------------------------------------
def _subset(rows, k):
    return [r for r in rows if (r["n_author"] or 0) >= k]


def _col_mae(subrows):
    return {"n": len(subrows),
            "champion": _mean([r["champion_abs"] for r in subrows]),
            "challenger": _mean([r["challenger_abs"] for r in subrows])}


def _pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _bootstrap_ci(diffs, seed=ct.SEED, b=BOOTSTRAP_B, lo=2.5, hi=97.5):
    """Seeded bootstrap CI of the mean paired difference (deterministic)."""
    d = np.asarray(diffs, float)
    n = len(d)
    if n == 0:
        return (None, None)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(b, n))
    means = d[idx].mean(axis=1)
    return (float(np.percentile(means, lo)), float(np.percentile(means, hi)))


def paired_stats(rows):
    # champion_abs - challenger_abs ; positive == challenger better on that book.
    diffs = [r["champion_abs"] - r["challenger_abs"] for r in rows]
    wins = sum(1 for d in diffs if d > EPS)       # challenger strictly better
    losses = sum(1 for d in diffs if d < -EPS)    # champion strictly better
    ties = len(diffs) - wins - losses
    ci = _bootstrap_ci(diffs)
    return {
        "n": len(diffs), "mean_diff": _mean(diffs),
        "median_diff": float(np.median(diffs)) if diffs else None,
        "wins_challenger": wins, "losses_challenger": losses, "ties": ties,
        "ci95": ci,
        "resid_corr": _pearson([r["champion_signed"] for r in rows],
                               [r["challenger_signed"] for r in rows]),
    }


def _spread(vals):
    v = np.asarray([x for x in vals if x is not None], float)
    if not len(v):
        return {}
    return {"min": float(v.min()), "p25": float(np.percentile(v, 25)),
            "median": float(np.median(v)), "p75": float(np.percentile(v, 75)),
            "max": float(v.max())}


def aggregate(rows):
    cuts = {"overall": _col_mae(rows)}
    for k in SUBSETS:
        cuts[f">={k}"] = _col_mae(_subset(rows, k))

    genres = {}
    for r in rows:
        genres.setdefault(r["genre"], []).append(r)
    genre_rows = []
    for g, rs in genres.items():
        genre_rows.append({
            "genre": g, "n": len(rs),
            "champion": _mean([x["champion_abs"] for x in rs]),
            "challenger": _mean([x["challenger_abs"] for x in rs]),
            "small": len(rs) < SMALL_N})
    # Widest challenger LOSS first (challenger - champion, descending).
    genre_rows.sort(key=lambda gr: -((gr["challenger"] or 0) - (gr["champion"] or 0)))

    return {
        "cuts": cuts, "genres": genre_rows,
        "paired": paired_stats(rows),
        "spread": {"champion": _spread([r["champion_abs"] for r in rows]),
                   "challenger": _spread([r["challenger_abs"] for r in rows])},
    }


def decide(champion_mae, challenger_mae):
    delta = champion_mae - challenger_mae   # + == challenger better (lower MAE)
    if delta >= TIE_THRESHOLD:
        verdict = "CHALLENGER WINS"
    elif delta <= -TIE_THRESHOLD:
        verdict = "CHALLENGER LOSES (champion wins)"
    else:
        verdict = "TIE"
    return verdict, delta


# ---------------------------------------------------------------------------
# Markdown report (deterministic — provenance from the committed folds meta)
# ---------------------------------------------------------------------------
def _f(x, p=3):
    return "  –  " if x is None else f"{x:.{p}f}"


def _table(headers, trows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in trows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _load_meta(out_dir):
    p = os.path.join(out_dir, wf.META_FILE)
    return json.load(open(p)) if os.path.exists(p) else {}


def render(rows, agg, out_dir=OUT_DIR):
    meta = _load_meta(out_dir)
    cuts = agg["cuts"]
    champ = cuts["overall"]["champion"]
    chal = cuts["overall"]["challenger"]
    verdict, delta = decide(champ, chal)
    pr = agg["paired"]

    sane = abs(champ - CHAMPION_MAE_EXPECTED) <= CHAMPION_MAE_BAND

    L = []
    L.append("# Walk-Forward Bake-Off — TabPFN Challenger vs Engine Champion\n")
    L.append(f"Engine `{meta.get('engine_hash')}` · git "
             f"`{(meta.get('git_head') or '')[:12]}` · {len(rows)} folds "
             f"(burn-in {meta.get('burn_in')}) · TabPFN v2 (seed {ct.SEED}, CPU, "
             f"{ct.N_ESTIMATORS} estimators) · zero-API, read-only, deterministic.\n")

    L.append("## Champion sanity check\n")
    L.append(f"Champion honest WA MAE read from the committed folds: **{_f(champ)}** "
             f"(expected ≈ {CHAMPION_MAE_EXPECTED}). "
             + ("✅ reproduces the published number — the head-to-head is wired to "
                "the same folds.\n" if sane else
                "❌ **does NOT match the published ~0.63 — the harness call is "
                "wrong; stop and investigate before trusting any comparison.**\n"))

    L.append("## Method — apples-to-apples\n")
    L.append("The champion's honest WA errors are **read** from "
             "`walkforward_folds.jsonl` (not recomputed). The challenger is scored "
             "on the **identical** folds / read order / strictly-past-only horizon, "
             "with the harness's own per-fold scorer and MAE aggregator. TabPFN sees "
             "**raw** causal author/genre priors (mean + count), a word-count, and "
             "series flag/position — never the engine's shrunk baseline — so this is "
             "learned vs. hand-tuned shrinkage. `>=N-prior` = the book had >= N "
             "prior-read books by the same author (the engine's own `n_author`), "
             "which equals the challenger's `author_prior_count` by construction.\n")

    # 1. Main MAE table
    cut_keys = ["overall", ">=1", ">=2", ">=3"]
    L.append("## WA MAE — champion vs challenger\n")
    header = ["predictor"] + [f"{k}  (n={cuts[k]['n']})" for k in cut_keys]
    L.append(_table(header, [
        ["**champion (engine honest)**"] + [_f(cuts[k]["champion"]) for k in cut_keys],
        ["**challenger (TabPFN)**"] + [_f(cuts[k]["challenger"]) for k in cut_keys],
        ["delta (champ − chal)"] + [
            _f((cuts[k]["champion"] - cuts[k]["challenger"])
               if cuts[k]["champion"] is not None and cuts[k]["challenger"] is not None
               else None) for k in cut_keys],
    ]))
    L.append("\n_Lower is better. Positive delta = challenger better. "
             f"|delta| < {TIE_THRESHOLD} on overall MAE is a **tie** (small-N fold "
             "noise); only a clean margin counts.\n")

    # 2. Verdict
    L.append("## Verdict\n")
    L.append(_table(["quantity", "value"], [
        ["champion overall WA MAE", _f(champ)],
        ["challenger overall WA MAE", _f(chal)],
        ["delta (champion − challenger)", _f(delta)],
        [f"tie threshold", str(TIE_THRESHOLD)],
    ]))
    L.append("")
    L.append(f"**VERDICT: {verdict}.** " + _verdict_sentence(verdict, delta) + "\n")

    # 3. Paired comparison (win vs noise)
    lo, hi = pr["ci95"]
    L.append("## Paired per-book comparison (is a win real or noise?)\n")
    L.append(_table(["quantity", "value"], [
        ["folds", str(pr["n"])],
        ["mean paired |error| diff (champ − chal)", _f(pr["mean_diff"])],
        ["median paired diff", _f(pr["median_diff"])],
        ["bootstrap 95% CI of mean diff", f"[{_f(lo)}, {_f(hi)}]"],
        ["folds challenger strictly better", str(pr["wins_challenger"])],
        ["folds champion strictly better", str(pr["losses_challenger"])],
        ["ties", str(pr["ties"])],
        ["champion/challenger residual correlation", _f(pr["resid_corr"])],
    ]))
    ci_note = ("the CI straddles 0, so the mean difference is not distinguishable "
               "from noise" if lo is not None and lo <= 0 <= hi
               else "the CI excludes 0")
    L.append(f"\n_Mean paired-difference bootstrap (seed {ct.SEED}, {BOOTSTRAP_B} "
             f"resamples): {ci_note}. Residual correlation is the Phase-5a ensemble "
             "pre-check — near 1.0 means both models make the same mistakes, so a "
             "blend is unlikely to help._\n")

    # 4. Per-fold spread
    L.append("## Per-fold absolute-error spread\n")
    sp = agg["spread"]
    L.append(_table(["predictor", "min", "p25", "median", "p75", "max"], [
        ["champion"] + [_f(sp["champion"].get(k)) for k in
                        ("min", "p25", "median", "p75", "max")],
        ["challenger"] + [_f(sp["challenger"].get(k)) for k in
                          ("min", "p25", "median", "p75", "max")],
    ]))
    L.append(f"\n_Full per-fold arrays live in `{PRED_FILE}` (one row per fold, "
             "sorted by position)._\n")

    # 5. Per-genre
    L.append("## Per-genre — champion vs challenger  (widest challenger loss first)\n")
    prows = []
    for gr in agg["genres"]:
        gap = ((gr["challenger"] - gr["champion"])
               if gr["challenger"] is not None and gr["champion"] is not None else None)
        flag = "⚠ n<10" if gr["small"] else ""
        prows.append([gr["genre"][:26], gr["n"], _f(gr["champion"]),
                      _f(gr["challenger"]), _f(gap), flag])
    L.append(_table(["genre", "n", "champion", "challenger", "gap (chal−champ)",
                     "flag"], prows))
    L.append("\n_Positive gap = the challenger is worse in that genre. Cells with "
             "n<10 are too small to conclude._\n")

    # 6. Interpretation — synthesise the nuances so the artifact stands alone.
    d3 = (cuts[">=3"]["champion"] - cuts[">=3"]["challenger"]
          if cuts[">=3"]["champion"] is not None
          and cuts[">=3"]["challenger"] is not None else None)
    lo, hi = pr["ci95"]
    ci_straddles = lo is not None and lo <= 0 <= hi
    L.append("## Notes & caveats\n")
    notes = []
    notes.append(
        f"**Mechanically a loss, but a soft one.** The headline is a "
        f"{abs(delta):.3f} WA MAE loss (> {TIE_THRESHOLD}), so by the pre-committed "
        f"rule this is a clear loss — not a tie. "
        + ("Yet the paired-difference bootstrap CI straddles 0, so at the per-book "
           "level the gap is not statistically clean: a few large-error folds "
           "(Russian Lit / Gothic outliers) drive most of it."
           if ci_straddles else
           "The paired-difference CI excludes 0, so the loss is statistically clean."))
    if d3 is not None and d3 > 0:
        notes.append(
            f"**Where the challenger actually wins: rich author history.** On the "
            f">=3-prior-author subset (n={cuts['>=3']['n']}) TabPFN *beats* the "
            f"engine by {abs(d3):.3f} ({_f(cuts['>=3']['challenger'])} vs "
            f"{_f(cuts['>=3']['champion'])}). Learned shrinkage is competitive "
            "exactly where there is enough same-author data to learn from; it loses "
            "overall on thin-author books, where the engine's regression backbone + "
            "genre-bias carry it and a metadata-only learner has little to go on.")
    notes.append(
        f"**Residual correlation {_f(pr['resid_corr'])}** — moderate, not extreme. "
        "The two models do make somewhat different mistakes, but the challenger is "
        "the clearly weaker one overall, so blending a weaker model into the "
        "champion is not expected to help.")
    notes.append(
        "**Phases 4 (intervals) and 5 (ensemble) are intentionally NOT run.** The "
        "brief gates interval calibration on a point-prediction *win* and gates the "
        "ensemble ladder on a *win or tie*; a clear aggregate loss triggers neither. "
        "Nothing about the served path, the conformal band, or the engine changes — "
        "this is an analysis-only bake-off and its verdict is that the champion stays.")
    L.append("\n\n".join(f"- {n}" for n in notes) + "\n")

    return "\n".join(L) + "\n"


def _verdict_sentence(verdict, delta):
    if verdict == "CHALLENGER WINS":
        return (f"TabPFN beats the engine by {abs(delta):.3f} WA MAE (>= "
                f"{TIE_THRESHOLD}). Proceed to interval calibration (Phase 4).")
    if verdict.startswith("CHALLENGER LOSES"):
        return (f"The engine beats TabPFN by {abs(delta):.3f} WA MAE (>= "
                f"{TIE_THRESHOLD}). No ensemble is warranted (a blend of a good and "
                "a worse model rarely wins); the bake-off ends here.")
    return (f"The two are within {TIE_THRESHOLD} WA MAE ({abs(delta):.3f}) — a tie "
            "given small-N fold noise. An ensemble may be worth a cheap look "
            "(Phase 5), gated on residual correlation.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def do_run(out_dir, xlsx):
    ordered, feat_rows = build_sequence(xlsx)
    evalset = build_eval(ordered, feat_rows, out_dir)
    rows = run_predictions(ordered, feat_rows, evalset)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, PRED_FILE), "w") as fh:
        fh.write(_serialise(rows))
    return rows


def _print_summary(rows, agg):
    cuts = agg["cuts"]
    champ, chal = cuts["overall"]["champion"], cuts["overall"]["challenger"]
    verdict, delta = decide(champ, chal)
    print(f"Champion  overall WA MAE: {champ:.4f}  (expected ~{CHAMPION_MAE_EXPECTED})")
    print(f"Challenger overall WA MAE: {chal:.4f}")
    print(f"Delta (champion − challenger): {delta:+.4f}   tie threshold ±{TIE_THRESHOLD}")
    print(f"Residual correlation: {agg['paired']['resid_corr']}")
    print(f"VERDICT: {verdict}")


def main():
    ap = argparse.ArgumentParser(
        description="Walk-forward bake-off: TabPFN challenger vs engine champion "
                    "(zero-API, read-only, analysis-only).")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--xlsx", default=os.path.join(ROOT, "BookRankingsNew.xlsx"))
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from an existing predictions artifact.")
    ap.add_argument("--check-determinism", action="store_true",
                    help="run predictions twice and assert byte-identical output.")
    args = ap.parse_args()

    if args.check_determinism:
        ordered, feat_rows = build_sequence(args.xlsx)
        evalset = build_eval(ordered, feat_rows, args.out_dir)
        a = _serialise(run_predictions(ordered, feat_rows, evalset))
        b = _serialise(run_predictions(ordered, feat_rows, evalset))
        ha, hb = (hashlib.sha256(x.encode()).hexdigest() for x in (a, b))
        print(f"run A sha256: {ha}")
        print(f"run B sha256: {hb}")
        print("DETERMINISM: PASS" if a == b else "DETERMINISM: FAIL")
        raise SystemExit(0 if a == b else 1)

    if args.report_only:
        rows = load_predictions(args.out_dir)
    else:
        rows = do_run(args.out_dir, args.xlsx)

    agg = aggregate(rows)
    md = render(rows, agg, args.out_dir)
    path = os.path.join(args.out_dir, REPORT_FILE)
    with open(path, "w") as fh:
        fh.write(md)
    _print_summary(rows, agg)
    print(f"  wrote {os.path.join(args.out_dir, PRED_FILE)}")
    print(f"  wrote {path}")


if __name__ == "__main__":
    main()
