"""
series_features_eval.py
=======================
Walk-forward evaluation of the within-series prediction signal (series_signal.py)
against the honest baseline. Answers the ONE question the brief cares about:
does a series feature beat honest WA MAE 0.63 on the subset where it is active,
without wrecking calibration?

WHAT IT DOES
------------
Reuses the walk-forward harness's read-only, zero-API, deterministic machinery
(walkforward.build_order / _load_inputs / _install_no_api_guard + the past-only
pool discipline) WITHOUT editing it, and evaluates four variants per fold:

  honest              -- the walk-forward baseline (series_mode=None); reproduces
                         walkforward.py's honest number exactly.
  honest+level        -- + series level  (series mean)
  honest+trajectory   -- + series trajectory (last volume + slope)
  honest+both         -- + OLS line at the target ordinal

Every variant is the SAME live pipeline (research_predict.correct_and_predict) on
the SAME past-only pool; only series_mode differs. The series signal therefore
draws only from books read strictly before the target — identical leakage
discipline to the harness. books.db is never written.

LEAKAGE NOTE: series_number is intrinsic, non-temporal metadata (a book's ordinal
is known before reading), so the full-DB snum_map is used for the trajectory
x-axis; the WA *values* the signal blends toward come only from the past-only
pool. The target title is excluded from its own series pool.

RUN
---
    python3 series_features_eval.py            # eval + write validation/series_features_eval.md
    python3 series_features_eval.py --check-determinism
"""

import argparse
import hashlib
import json
import os
from collections import defaultdict

import numpy as np

import walkforward as wf
import research_predict as rp
import predict_engine as pe
import series_signal as ss
import intervals

LIVE = wf.LIVE
OUT_DIR = wf.OUT_DIR
REPORT = "series_features_eval.md"
BURN_IN = wf.BURN_IN_DEFAULT
NOMINAL = 0.80  # the served conformal band's target (CLAUDE.md)

VARIANTS = ("honest", "honest+level", "honest+trajectory", "honest+both")
MODE_OF = {"honest": None, "honest+level": "level",
           "honest+trajectory": "trajectory", "honest+both": "both"}


# ---------------------------------------------------------------------------
# Fold evaluation
# ---------------------------------------------------------------------------
def run(burn_in=BURN_IN):
    wf._install_no_api_guard()
    books, gw, gcw, cache = wf._load_inputs()
    xlsx = os.path.join(wf.ROOT, "BookRankingsNew.xlsx")
    order, _ = wf.build_order(books, xlsx)
    snum_map = ss.build_snum_map(wf.db_loader.DB)

    folds = []
    for entry in order:
        pos, title = entry["position"], entry["title"]
        if pos <= burn_in:
            continue
        if title not in cache or not isinstance(cache[title].get("scores"), dict):
            continue
        raw_scores = {c: float(cache[title]["scores"][c])
                      for c in LIVE if c in cache[title]["scores"]}
        if len(raw_scores) != len(LIVE):
            continue
        conf = cache[title].get("conf", "?")

        row = books[books["Book"] == title].iloc[0]
        actual_wa = float(row["WA"])
        author, genre = entry["author"], entry["genre"]
        series, series_number = entry["series"], entry["series_number"]

        # Past-only pool = books read strictly before this one (the honest pool).
        pool_titles = [e["title"] for e in order if e["position"] < pos]
        books_pool = books[books["Book"].isin(pool_titles)]
        resid_sd = pe.fit_regression(books_pool)[2]
        corr_models = rp.build_corr_models(books_pool, cache)

        # How many prior in-series reads the signal can see (real series only).
        priors = ss.prior_volumes(title, series, books_pool, snum_map)
        n_prior = len(priors)
        n_distinct_ord = len({o for o, _ in priors if o is not None})

        rec = {"position": pos, "title": title, "author": author, "genre": genre,
               "series": series if ss.is_real_series(series) else None,
               "series_number": series_number, "actual_wa": round(actual_wa, 6),
               "n_prior": n_prior, "n_distinct_ord": n_distinct_ord,
               "n_author": None, "variants": {}}

        for vname in VARIANTS:
            res = rp.correct_and_predict(
                title, author, genre, dict(raw_scores), conf, resid_sd,
                books_pool, gw, gcw, cache, corr_models=corr_models,
                series=series, series_number=series_number,
                series_mode=MODE_OF[vname], snum_map=snum_map)
            wa = res["wa"]
            rec["n_author"] = res["n_author"]
            info = res.get("series_adjust")
            rec["variants"][vname] = {
                "wa": round(wa, 6),
                "signed_err": round(wa - actual_wa, 6),
                "abs_err": round(abs(wa - actual_wa), 6),
                "active": bool(info["active"]) if info else False,
                "weight": round(info["weight"], 4) if info and info["active"] else 0.0,
                "target": round(info["target"], 4) if info and info["active"] else None,
            }
        folds.append(rec)

    folds.sort(key=lambda f: f["position"])
    return folds


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _mae(folds, v):
    xs = [f["variants"][v]["abs_err"] for f in folds]
    return sum(xs) / len(xs) if xs else None


def _fmt(x, p=3):
    return "  –  " if x is None else f"{x:.{p}f}"


def subset(folds, min_prior):
    """Folds where the series signal is active with >= min_prior prior in-series
    reads (a real series)."""
    return [f for f in folds if f["series"] is not None and f["n_prior"] >= min_prior]


def served_coverage(folds, variant, residuals_path):
    """Served-conformal coverage on this variant's errors: bucket each fold by its
    honest same-author analog count (n_author) exactly as the live serving path,
    look up that bucket's conformal half-width, and check whether the variant's WA
    error falls inside. This is the interval a reader actually sees."""
    table = intervals.load_residuals(residuals_path)
    if not table:
        return None
    hits = tot = 0
    for f in folds:
        na = f["n_author"]
        if na is None:
            continue
        info = intervals.interval_for(table, na)
        if not info:
            continue
        tot += 1
        if abs(f["variants"][variant]["signed_err"]) <= info["half_width"] + 1e-12:
            hits += 1
    return {"coverage": hits / tot if tot else None, "n": tot}


def paired(folds, v):
    """Per-fold paired effect vs honest on the given fold set: helped/hurt counts
    and mean abs-error reduction (positive = variant beats honest)."""
    helped = hurt = 0
    deltas = []
    for f in folds:
        d = f["variants"]["honest"]["abs_err"] - f["variants"][v]["abs_err"]
        deltas.append(d)
        if d > 1e-9:
            helped += 1
        elif d < -1e-9:
            hurt += 1
    return {"helped": helped, "hurt": hurt, "n": len(folds),
            "mean_delta": sum(deltas) / len(deltas) if deltas else None}


def by_fold(folds, v, k=4):
    """Individual folds most helped / most hurt by variant v vs honest (active
    folds only) — named examples for the interpretation section."""
    active = [f for f in folds if f["series"] is not None and f["variants"][v]["active"]]
    scored = [(f["variants"]["honest"]["abs_err"] - f["variants"][v]["abs_err"], f)
              for f in active]
    scored.sort(key=lambda t: t[0])
    def fmt(d, f):
        vv = f["variants"][v]
        return {"title": f["title"], "series": f["series"], "actual": f["actual_wa"],
                "honest": f["variants"]["honest"]["wa"], "variant": vv["wa"],
                "delta": d}
    helped = [fmt(d, f) for d, f in scored[::-1][:k] if d > 1e-6]
    hurt = [fmt(d, f) for d, f in scored[:k] if d < -1e-6]
    return {"helped": helped, "hurt": hurt}


def by_series(folds, v):
    """Per-series mean abs-error reduction vs honest (active folds only), for the
    'names names' section. Only series with >=1 active fold."""
    groups = defaultdict(list)
    for f in folds:
        if f["series"] is not None and f["variants"][v]["active"]:
            groups[f["series"]].append(
                f["variants"]["honest"]["abs_err"] - f["variants"][v]["abs_err"])
    rows = [{"series": s, "n": len(ds), "mean_delta": sum(ds) / len(ds)}
            for s, ds in groups.items()]
    rows.sort(key=lambda r: r["mean_delta"])  # most-hurt first, most-helped last
    return rows


def analysis(folds, residuals_path):
    a = {"n_folds": len(folds),
         "n_series_folds": len([f for f in folds if f["series"] is not None]),
         "overall": {v: _mae(folds, v) for v in VARIANTS},
         "subsets": {}, "genre": {}, "paired": {}, "coverage": {}, "series": {}}
    for k in (1, 2, 3):
        s = subset(folds, k)
        a["subsets"][k] = {"n": len(s), **{v: _mae(s, v) for v in VARIANTS}}
    # per-genre on the active subset (>=1 prior)
    active = subset(folds, 1)
    gg = defaultdict(list)
    for f in active:
        gg[f["genre"]].append(f)
    for g, fs in gg.items():
        a["genre"][g] = {"n": len(fs), **{v: _mae(fs, v) for v in VARIANTS}}
    # paired effect per variant on the active subset
    a["fold_examples"] = {}
    for v in VARIANTS[1:]:
        a["paired"][v] = paired(active, v)
        a["series"][v] = by_series(folds, v)
        a["fold_examples"][v] = by_fold(folds, v)
    # calibration: served coverage per variant (all folds)
    for v in VARIANTS:
        a["coverage"][v] = served_coverage(folds, v, residuals_path)
    return a


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _decide(a):
    """Ship/no-ship per variant, on numbers only. A variant ships iff it improves
    honest WA MAE on its active subset (>=1 prior) AND does not degrade served
    calibration below the honest baseline by more than a hair."""
    base_cov = a["coverage"]["honest"]["coverage"] if a["coverage"]["honest"] else None
    decisions = {}
    for v in VARIANTS[1:]:
        sub = a["subsets"][1]
        better = (sub[v] is not None and sub["honest"] is not None
                  and sub[v] < sub["honest"] - 1e-6)
        cov = a["coverage"][v]["coverage"] if a["coverage"][v] else None
        cov_ok = (cov is None or base_cov is None or cov >= base_cov - 0.03)
        decisions[v] = {
            "ship": bool(better and cov_ok),
            "delta_active": (sub[v] - sub["honest"]) if better or sub[v] else
                            (sub[v] - sub["honest"] if sub[v] is not None and sub["honest"] is not None else None),
            "cov": cov, "cov_ok": cov_ok}
    return decisions


def render(a, meta):
    L = []
    L.append("# Series-Trend Features — Walk-Forward Evaluation\n")
    L.append(f"Engine `{meta['engine_hash']}` · {a['n_folds']} folds "
             f"(burn-in {meta['burn_in']}), {a['n_series_folds']} in a real series · "
             f"zero-API, read-only, deterministic.\n")
    L.append("Baseline = **honest** (walk-forward, `series_mode=None`) — reproduces "
             "`walkforward.py`'s honest MAE exactly. Each `+variant` is the SAME "
             "pipeline on the SAME past-only pool with only the series nudge added "
             "(`series_signal.py`): **level** = series mean · **trajectory** = last "
             "volume + slope · **both** = OLS line at the target ordinal. Combination "
             f"weight `n/(n+K_SERIES)`, K_SERIES={ss.K_SERIES} (pre-registered, not "
             "tuned to this set).\n")

    # 1. Overall (diluted by standalones — see subsets)
    L.append("## Overall WA MAE (all folds — diluted by standalones/first volumes)\n")
    L.append(_table(["variant", "WA MAE", "Δ vs honest"],
             [[v, _fmt(a["overall"][v]),
               _fmt(a["overall"][v] - a["overall"]["honest"]) if v != "honest" else "—"]
              for v in VARIANTS]))
    L.append("\n_Most folds are standalones or first volumes where the feature is a "
             "no-op, so the global number barely moves. The active-subset view below "
             "is the real test._\n")

    # 2. Active subsets
    L.append("## WA MAE on the ACTIVE subset (series books with ≥N prior in-series reads)\n")
    rows = []
    for k in (1, 2, 3):
        s = a["subsets"][k]
        rows.append([f"≥{k} prior", s["n"]] +
                    [_fmt(s[v]) for v in VARIANTS] +
                    [_fmt(s["honest+both"] - s["honest"]) if s["honest"] is not None else "–"])
    L.append(_table(["subset", "n", "honest", "+level", "+trajectory", "+both", "Δ both−honest"], rows))
    L.append("")

    # 3. Paired effect
    L.append("## Paired effect on the active subset (≥1 prior) — per-fold vs honest\n")
    L.append(_table(["variant", "n", "helped", "hurt", "mean |err| reduction"],
             [[v, a["paired"][v]["n"], a["paired"][v]["helped"], a["paired"][v]["hurt"],
               _fmt(a["paired"][v]["mean_delta"], 4)] for v in VARIANTS[1:]]))
    L.append("\n_Positive reduction = the variant beats honest. helped/hurt counts "
             "how many active folds each way._\n")

    # 4. Per-genre (active subset)
    L.append("## WA MAE by genre — active subset (≥1 prior)\n")
    grows = sorted(a["genre"].items(), key=lambda kv: -kv[1]["n"])
    L.append(_table(["genre", "n", "honest", "+level", "+trajectory", "+both"],
             [[g[:26], d["n"]] + [_fmt(d[v]) for v in VARIANTS] for g, d in grows]))
    L.append("")

    # 5. Per-series (names names)
    L.append("## Per-series effect — where the feature helps / hurts (mean |err| reduction vs honest)\n")
    for v in VARIANTS[1:]:
        rows = a["series"][v]
        if not rows:
            continue
        helps = [r for r in rows if r["mean_delta"] > 1e-6][-6:][::-1]
        hurts = [r for r in rows if r["mean_delta"] < -1e-6][:6]
        L.append(f"**{v}** — helps most: " +
                 (", ".join(f"{r['series']} ({r['mean_delta']:+.3f}, n={r['n']})" for r in helps) or "none") + ".")
        L.append(f"  hurts most: " +
                 (", ".join(f"{r['series']} ({r['mean_delta']:+.3f}, n={r['n']})" for r in hurts) or "none") + ".\n")

    # 5b. Interpretation (data-driven named examples)
    L.append("## Interpretation — why the mean-pull nets to no gain\n")
    ex = a["fold_examples"]["honest+level"]
    def _names(items):
        return "; ".join(
            f"{e['title']} ({e['series']}): honest {e['honest']:.2f} → "
            f"{e['variant']:.2f}, actual {e['actual']:.2f}" for e in items) or "none"
    L.append("The series signal is a pull toward the series' own level/trend. It "
             "**helps** where the author-blended base under-shot a strong series — "
             f"{_names(ex['helped'])}. It **hurts** where a specific volume broke "
             f"from the series norm, or where honest was already accurate and the "
             f"mean-pull added noise — {_names(ex['hurt'])}.\n")
    ef = a["genre"].get("Epic Fantasy")
    if ef and ef.get("honest") is not None and ef.get("honest+level") is not None:
        L.append(f"- **Epic Fantasy is the one bright spot** (the brief's expected home "
                 f"for series depth): level improves it {ef['honest']:.3f} → "
                 f"{ef['honest+level']:.3f} on n={ef['n']} — but the identical "
                 "mechanism *hurts* the SF series (Ender's Game/Shadow, Hyperion), so "
                 "the all-genre active subset nets flat. Acting on the EF slice alone "
                 "would be fitting to this test set.\n")
    L.append("- **For single-series authors (Malazan/Erikson, WoT/Jordan) the series "
             "level *duplicates* the author mean** — they wrote only that series here, so "
             "the level target equals the author deviation the correction already uses "
             "(median series-vs-author level gap 0.00 WA, Phase 0.3). The pull still "
             "*moves* these predictions toward that flat mean, but as pure "
             "regression-to-the-mean it helps and hurts about equally and nets to zero "
             "per series. The only genuinely incremental cases are multi-series authors "
             "(Sanderson), where the series level ≠ the author mean.\n")
    L.append("- **Trajectory is worse than level**, not better: extrapolating a 2–4 "
             "point WA trend adds variance rather than signal at this library size — "
             "series volumes swing around their trend more than the slope predicts.\n")

    # 6. Calibration
    L.append(f"## Calibration — served conformal coverage (target {NOMINAL:.0%})\n")
    L.append("Does the point-estimate nudge wreck the served interval? Bucket each "
             "fold by its author-analog count and check the variant's WA error against "
             "that bucket's conformal half-width (`calibration/residuals.json`).\n")
    crows = []
    for v in VARIANTS:
        c = a["coverage"][v]
        crows.append([v, f"{c['coverage']:.1%}" if c and c["coverage"] is not None else "–",
                      c["n"] if c else "–"])
    L.append(_table(["variant", "served coverage", "n"], crows))
    L.append("")

    # 7. Decision
    dec = _decide(a)
    L.append("## Ship / no-ship decision\n")
    drows = []
    for v in VARIANTS[1:]:
        d = dec[v]
        sub = a["subsets"][1]
        drows.append([v, _fmt(sub["honest"]), _fmt(sub[v]),
                      _fmt(sub[v] - sub["honest"]) if sub[v] is not None else "–",
                      f"{d['cov']:.1%}" if d["cov"] is not None else "–",
                      "**SHIP**" if d["ship"] else "no-ship"])
    L.append(_table(["variant", "honest (≥1)", "variant (≥1)", "Δ MAE", "coverage", "decision"], drows))
    any_ship = any(dec[v]["ship"] for v in VARIANTS[1:])
    L.append("")
    if any_ship:
        winners = [v for v in VARIANTS[1:] if dec[v]["ship"]]
        L.append(f"**Recommendation: SHIP {', '.join(winners)}** — improves honest WA "
                 "MAE on the active subset without degrading served calibration.\n")
    else:
        L.append("**Recommendation: SHIP NOTHING.** No variant improves honest WA MAE "
                 "on its active subset. This confirms the Phase 0.3 hypothesis: the "
                 "series *level* is already captured by the author pool (median "
                 "series-vs-author level gap 0.00 WA), and the *trajectory* does not "
                 "beat the running mean at this library size — the actuals are too "
                 "noisy around the trend. A valid, useful negative result: keep the "
                 "eval as the record and change nothing served.\n")
        L.append("**Most promising future lead** (do NOT act on it from this run — that "
                 "would be fitting to the test): the series *level* concentrates its "
                 "benefit on Epic Fantasy / multi-series authors. A genre- or "
                 "multi-series-gated level, with K_SERIES chosen by proper *nested* "
                 "cross-validation and re-checked as the library grows, is the right "
                 "way to test that — not by slicing this walk-forward set post hoc.\n")
    return "\n".join(L) + "\n", dec


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _meta(burn_in):
    return {"engine_hash": wf._engine_hash(), "git_head": wf._git_head(),
            "burn_in": burn_in}


def main():
    ap = argparse.ArgumentParser(description="Walk-forward eval of the series signal.")
    ap.add_argument("--burn-in", type=int, default=BURN_IN)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--check-determinism", action="store_true")
    args = ap.parse_args()

    residuals_path = os.path.join(wf.ROOT, "calibration", "residuals.json")

    if args.check_determinism:
        a = run(args.burn_in)
        b = run(args.burn_in)
        ha = hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest()
        hb = hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()
        print(f"run A {ha}\nrun B {hb}")
        raise SystemExit(0 if ha == hb else 1)

    folds = run(args.burn_in)
    a = analysis(folds, residuals_path)
    md, dec = render(a, _meta(args.burn_in))
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, REPORT), "w") as fh:
        fh.write(md)
    print(md)
    print(f"  wrote {os.path.join(args.out_dir, REPORT)}")


if __name__ == "__main__":
    main()
