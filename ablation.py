"""
ablation.py
===========
Engine ablation study — does the machinery earn its complexity?

THE ONE QUESTION
----------------
The prediction engine is elaborate: 14 grounded-research components, correlation
smoothing, empirical-Bayes author+genre shrinkage, per-genre category weights, a
WA roll-up. Does all of that actually beat a dead-simple metadata-only baseline —
"you already know this author, predict their running average" — on the honest
walk-forward folds? If it doesn't, the complexity is ceremony.

This is ANALYSIS ONLY. It ships nothing, changes no engine math, writes no
prediction. It reuses the walk-forward harness's read-only, deterministic,
zero-API machinery WITHOUT editing it, and scores four naive baselines on the
EXACT SAME folds / order / past-only pool discipline as the engine's `honest`
variant, so the comparison is apples-to-apples.

THE PRE-COMMITTED DECISION RULE  (fixed BEFORE any number was seen)
------------------------------------------------------------------
The full engine "earns its complexity" if it beats the **author-mean baseline**
by **>= 0.05 WA MAE on the >=3-prior-reads subset** AND is not *worse* than
author-mean on overall honest MAE. If it beats by 0.02-0.05, the verdict is
"marginal - complexity is mostly ceremony". If it beats by <0.02 or loses
anywhere material, the verdict is "the machinery is not paying for itself".

The verdict is stated MECHANICALLY from the numbers below - not editorialised.

THE FOUR BASELINES  (each a pure fn of the past-only pool + held-out author/genre)
---------------------------------------------------------------------------------
  global-mean    -- mean WA of ALL prior-read books. The floor.
  genre-mean     -- mean WA of prior-read books in the same genre; else global.
  author-mean    -- mean WA of prior-read books by the same author; else genre,
                    else global. THE PRIMARY COMPARATOR ("you know this author").
  author+genre   -- simple average of author-mean and genre-mean where both
                    exist; else whichever exists; else global.

Leakage discipline is identical to the engine's honest variant: the pool is
books read STRICTLY BEFORE the held-out book (positions 1..t-1 in Timeline read
order), and author/genre are matched by exact string equality on the same frame
the engine uses (mirroring research_predict.py's `n_author =
(df["Author"] == author).sum()`), so a baseline's author count equals the
engine's `n_author` for the same fold - the subsets line up exactly.

WHY IT CANNOT DRIFT FROM THE COMMITTED ENGINE NUMBER
----------------------------------------------------
The engine's `honest` WA errors are READ from the committed folds artifact
(validation/walkforward_folds.jsonl), not recomputed - so the engine column is,
by construction, the same 0.63 the harness published. The baselines are scored
with the harness's own per-fold scorer (walkforward._r + abs) and aggregated with
the harness report's own MAE mean (walkforward_report._mean). No reimplementation.

RUN
---
    python3 ablation.py                    # eval + write validation/ablation.md
    python3 ablation.py --check-determinism # prove two renders are byte-identical

Artifacts land in validation/ (NOT a static-snapshot input - offline analysis).
"""

import argparse
import hashlib
import json
import os

import db_loader
import walkforward as wf
import walkforward_report as wr

ROOT = wf.ROOT
OUT_DIR = wf.OUT_DIR
REPORT_FILE = "ablation.md"

# Reuse the harness's own scorer + aggregator verbatim (brief 0.3 / Task 1).
_r = wf._r          # 6-dp deterministic round == the per-fold scorer's rounding
_mean = wr._mean    # None-filtering mean == the MAE aggregator that produced 0.63

SUBSETS = (1, 2, 3)          # >=N prior reads BY THE SAME AUTHOR (engine's n_author)
SMALL_N = 10                 # a genre cell below this is "too small to conclude"

# The pre-committed rule, printed verbatim into the report (brief Task 2).
DECISION_RULE = (
    "The full engine \"earns its complexity\" if it beats the **author-mean "
    "baseline** by **>= 0.05 WA MAE on the >=3-prior-reads subset** AND is not "
    "*worse* than author-mean on overall honest MAE. If it beats by 0.02-0.05, "
    "verdict is \"marginal - complexity is mostly ceremony\". If it beats by "
    "<0.02 or loses anywhere material, verdict is \"the machinery is not paying "
    "for itself\"."
)


# ---------------------------------------------------------------------------
# The four baselines - pure functions of (pool, held-out author, held-out genre)
# `pool` is a list of (author, genre, wa) triples for books read strictly before
# the held-out book. Each returns a WA prediction (or None if the pool is empty).
# ---------------------------------------------------------------------------
def _mean_wa(pool):
    return _mean([wa for _a, _g, wa in pool])


def global_mean(pool, author, genre):
    return _mean_wa(pool)


def genre_mean(pool, author, genre):
    same = [(a, g, wa) for (a, g, wa) in pool if g == genre]
    return _mean_wa(same) if same else global_mean(pool, author, genre)


def author_mean(pool, author, genre):
    same = [(a, g, wa) for (a, g, wa) in pool if a == author]
    if same:
        return _mean_wa(same)
    return genre_mean(pool, author, genre)


def author_genre_blend(pool, author, genre):
    a_wa = _mean_wa([(a, g, wa) for (a, g, wa) in pool if a == author])
    g_wa = _mean_wa([(a, g, wa) for (a, g, wa) in pool if g == genre])
    if a_wa is not None and g_wa is not None:
        return (a_wa + g_wa) / 2.0
    if a_wa is not None:
        return a_wa
    if g_wa is not None:
        return g_wa
    return global_mean(pool, author, genre)


# Insertion order == column order in the report.
BASELINES = {
    "global-mean": global_mean,
    "genre-mean": genre_mean,
    "author-mean": author_mean,
    "author+genre": author_genre_blend,
}
PRIMARY = "author-mean"          # the pre-committed comparator


# ---------------------------------------------------------------------------
# Build the evaluation set: the SAME folds the harness evaluated, each carrying
# its committed honest error + n_author, plus the reconstructed past-only pool.
# ---------------------------------------------------------------------------
def build_eval(out_dir=OUT_DIR, xlsx=None):
    """Return (rows, meta). Each row is one evaluated fold with:
        position, title, author, genre, actual_wa,
        honest_abs (committed engine error), n_author (engine's own count),
        baseline_abs = {name: wa_abs_error} for the four baselines.
    Consistency assertions guarantee the reconstructed pool aligns with the
    harness (same actual WA, same author count) - so this is truly apples-to-apples."""
    xlsx = xlsx or os.path.join(ROOT, "BookRankingsNew.xlsx")

    # Read-only load, exactly as the harness does. The no-API guard is belt-and-
    # braces (these baselines never construct a client), zero cost.
    wf._install_no_api_guard()
    books, _gw, _gcw = db_loader.load_from_db()
    order, _ = wf.build_order(books, xlsx)

    # Single source for per-book (author, genre, WA), keyed by title.
    meta_by_title = {}
    for _i, row in books.iterrows():
        meta_by_title[row["Book"]] = (
            row["Author"], row["Genre"],
            float(row["WA"]) if row["WA"] is not None else None)
    seq = sorted(((e["position"], e["title"]) for e in order), key=lambda t: t[0])

    folds, _skips = wf.load_folds(out_dir)
    meta_path = os.path.join(out_dir, wf.META_FILE)
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}

    rows = []
    for f in folds:
        pos, title = f["position"], f["title"]
        author, genre = f["author"], f["genre"]
        actual_wa = f["actual_wa"]

        # Reconstruct the past-only pool == books read strictly before `pos`.
        pool = []
        for p, t in seq:
            if p >= pos:
                break
            a, g, wa = meta_by_title[t]
            if wa is not None:
                pool.append((a, g, wa))

        # Apples-to-apples assertions: the pool we rebuilt must match the harness.
        eng_n_author = f["variants"]["honest"]["n_author"]
        my_n_author = sum(1 for a, _g, _wa in pool if a == author)
        if eng_n_author is not None and my_n_author != eng_n_author:
            raise SystemExit(
                f"POOL MISMATCH at pos {pos} ({title!r}): reconstructed "
                f"n_author={my_n_author} but harness recorded {eng_n_author}. "
                "Aborting rather than emit a misleading comparison.")
        recon_wa = meta_by_title[title][2]
        if recon_wa is None or abs(recon_wa - actual_wa) > 1e-4:
            raise SystemExit(
                f"ACTUAL-WA MISMATCH at pos {pos} ({title!r}): frame={recon_wa} "
                f"vs folds={actual_wa}. Aborting.")

        baseline_abs = {}
        for name, fn in BASELINES.items():
            pred = fn(pool, author, genre)
            baseline_abs[name] = _r(abs(pred - actual_wa)) if pred is not None else None

        rows.append({
            "position": pos, "title": title, "author": author, "genre": genre,
            "actual_wa": actual_wa,
            "honest_abs": f["variants"]["honest"]["wa_abs_error"],
            "n_author": eng_n_author if eng_n_author is not None else my_n_author,
            "baseline_abs": baseline_abs,
        })

    rows.sort(key=lambda r: r["position"])
    return rows, meta


# ---------------------------------------------------------------------------
# Aggregation - all MAEs via the harness report's own _mean.
# ---------------------------------------------------------------------------
def _subset(rows, min_prior):
    return [r for r in rows if (r["n_author"] or 0) >= min_prior]


def _col_mae(subrows):
    """MAE for engine honest + every baseline over one fold subset."""
    out = {"n": len(subrows),
           "engine": _mean([r["honest_abs"] for r in subrows])}
    for name in BASELINES:
        out[name] = _mean([r["baseline_abs"][name] for r in subrows])
    return out


def aggregate(rows):
    cuts = {"overall": _col_mae(rows)}
    for k in SUBSETS:
        cuts[f">={k}"] = _col_mae(_subset(rows, k))

    # Per-genre: engine honest vs the primary comparator (author-mean), over all
    # folds in the genre, with n + small-sample flag.
    genres = {}
    for r in rows:
        genres.setdefault(r["genre"], []).append(r)
    genre_rows = []
    for g, rs in genres.items():
        genre_rows.append({
            "genre": g, "n": len(rs),
            "engine": _mean([x["honest_abs"] for x in rs]),
            PRIMARY: _mean([x["baseline_abs"][PRIMARY] for x in rs]),
            "small": len(rs) < SMALL_N,
        })
    # Widest engine-loss (engine - author-mean, descending) first: names the
    # weak spots the brief asks for (literary / Russian lit suspected).
    genre_rows.sort(key=lambda gr: -((gr["engine"] or 0) - (gr[PRIMARY] or 0)))

    return {"cuts": cuts, "genres": genre_rows}


def decide(cuts):
    """Mechanical verdict from the pre-committed rule. Returns
    (verdict, imp3, imp_overall, detail-dict). Positive improvement = engine beats
    author-mean (lower MAE)."""
    eng3, auth3 = cuts[">=3"]["engine"], cuts[">=3"][PRIMARY]
    engO, authO = cuts["overall"]["engine"], cuts["overall"][PRIMARY]
    imp3 = auth3 - eng3            # + => engine better on the decision subset
    imp_overall = authO - engO    # + => engine better overall
    not_worse_overall = imp_overall >= -1e-9
    # "loses anywhere material" - operationalised as engine worse than author-mean
    # by >= 0.02 WA MAE on EITHER the overall cut or the >=3 decision subset.
    loses_material = (imp3 <= -0.02) or (imp_overall <= -0.02)

    if loses_material:
        verdict = "THE MACHINERY IS NOT PAYING FOR ITSELF"
    elif imp3 >= 0.05 and not_worse_overall:
        verdict = "THE ENGINE EARNS ITS COMPLEXITY"
    elif imp3 >= 0.02:
        verdict = "MARGINAL - COMPLEXITY IS MOSTLY CEREMONY"
    else:
        verdict = "THE MACHINERY IS NOT PAYING FOR ITSELF"
    return verdict, {
        "eng3": eng3, "auth3": auth3, "imp3": imp3,
        "engO": engO, "authO": authO, "imp_overall": imp_overall,
        "not_worse_overall": not_worse_overall, "loses_material": loses_material,
    }


# ---------------------------------------------------------------------------
# Markdown rendering (deterministic - no wall-clock; provenance from meta)
# ---------------------------------------------------------------------------
def _f(x, p=3):
    return "  –  " if x is None else f"{x:.{p}f}"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render(rows, agg, meta):
    cuts = agg["cuts"]
    verdict, d = decide(cuts)
    L = []
    L.append("# Engine Ablation — Does the Machinery Earn Its Complexity?\n")
    L.append(f"Engine `{meta.get('engine_hash')}` · git "
             f"`{(meta.get('git_head') or '')[:12]}` · {len(rows)} walk-forward "
             f"folds (burn-in {meta.get('burn_in')}) · zero-API, read-only, "
             "deterministic.\n")

    L.append("## The pre-committed decision rule (fixed before any number was seen)\n")
    L.append("> " + DECISION_RULE + "\n")
    L.append("_Verdict below is stated mechanically from the numbers. A \"not "
             "paying for itself\" outcome is a fully valid, valuable result — it "
             "is not softened._\n")

    L.append("## Method — apples-to-apples\n")
    L.append("Every baseline is scored on the **identical** folds, read order and "
             "past-only pool as the engine's `honest` variant. The engine's honest "
             "WA errors are **read** from the committed `walkforward_folds.jsonl` "
             "(not recomputed, so the engine column is the published 0.63). "
             "Baselines are metadata-only — mean WA over the prior-read pool, "
             "matching author/genre by exact string equality on the same frame the "
             "engine uses — so a baseline's author count **equals** the engine's "
             "`n_author` and the `>=N-prior` subsets line up exactly. Baselines are "
             "scored with the harness's own per-fold scorer and MAE aggregator; no "
             "metric is reimplemented.\n")
    L.append("`>=N-prior` = the held-out book had **>= N prior-read books by the "
             "same author** in its pool (the engine's own `n_author`). Overall is "
             "diluted by first-of-author books (n_author=0) where a metadata "
             "baseline has nothing author-specific to say and the engine *should* "
             "win — the author-prior subsets are the meaningful test.\n")

    # 1. The MAE table (baselines + engine honest x overall/>=1/>=2/>=3)
    cut_keys = ["overall", ">=1", ">=2", ">=3"]
    L.append("## WA MAE — baselines vs engine `honest`\n")
    header = ["predictor"] + [f"{k}  (n={cuts[k]['n']})" for k in cut_keys]
    trows = []
    for name in BASELINES:
        label = f"**{name}**" if name == PRIMARY else name
        trows.append([label] + [_f(cuts[k][name]) for k in cut_keys])
    trows.append(["**engine (honest)**"] + [_f(cuts[k]["engine"]) for k in cut_keys])
    L.append(_table(header, trows))
    L.append("\n_Lower is better. `global-mean` is the leakage-safe walk-forward "
             "floor (predict the running mean of all prior reads); note it is a "
             "stricter, per-fold baseline than `walkforward_report.py`'s "
             "`naive_meanWA`, which uses the whole set's mean._\n")

    # 2. The gap (engine - author-mean) per subset, signed
    L.append("## The gap: engine `honest` MAE − `author-mean` MAE, per subset\n")
    grows = []
    for k in cut_keys:
        eng, auth = cuts[k]["engine"], cuts[k][PRIMARY]
        gap = (eng - auth) if (eng is not None and auth is not None) else None
        sign = ("engine better" if gap is not None and gap < 0
                else "author-mean better" if gap is not None and gap > 0 else "—")
        grows.append([k, cuts[k]["n"], _f(eng), _f(auth), _f(gap), sign])
    L.append(_table(["subset", "n", "engine", "author-mean", "gap (eng−auth)",
                     "who wins"], grows))
    L.append("\n_Negative gap = the engine's machinery beats just-average-this-"
             "author. Positive = the naive author mean is as good or better._\n")

    # 3. Per-genre: engine vs author-mean, with n + small-sample flags
    L.append("## Per-genre — engine `honest` vs `author-mean`  (widest engine loss first)\n")
    prows = []
    for gr in agg["genres"]:
        eng, auth = gr["engine"], gr[PRIMARY]
        gap = (eng - auth) if (eng is not None and auth is not None) else None
        flag = "⚠ n<10 — too small to conclude" if gr["small"] else ""
        prows.append([gr["genre"][:28], gr["n"], _f(eng), _f(auth), _f(gap), flag])
    L.append(_table(["genre", "n", "engine", "author-mean", "gap (eng−auth)",
                     "sample-size flag"], prows))
    L.append("")
    big = [gr for gr in agg["genres"]
           if not gr["small"] and gr["engine"] is not None
           and gr[PRIMARY] is not None and (gr["engine"] - gr[PRIMARY]) > 0.02]
    if big:
        names = ", ".join(f"{gr['genre']} (+{gr['engine'] - gr[PRIMARY]:.3f}, n={gr['n']})"
                          for gr in big)
        L.append(f"**Genres (n>=10) where the machinery is beaten by the author "
                 f"mean:** {names}.\n")
    else:
        L.append("_No genre with n>=10 shows the author mean beating the engine by "
                 ">0.02; the per-genre signal is dominated by small cells (flagged), "
                 "which the rule forbids from driving the verdict._\n")

    # Name the brief's suspected weak spots explicitly (literary / Russian lit),
    # with n, so it is unambiguous they were inspected rather than glossed.
    suspects = [gr for gr in agg["genres"]
                if "Literary" in gr["genre"] or "Russian" in gr["genre"]]
    if suspects:
        parts = []
        for gr in suspects:
            gap = (gr["engine"] - gr[PRIMARY]) if (gr["engine"] is not None
                                                   and gr[PRIMARY] is not None) else None
            who = ("author-mean better" if gap is not None and gap > 0
                   else "engine better" if gap is not None and gap < 0 else "—")
            tag = " [n<10, too small]" if gr["small"] else ""
            parts.append(f"{gr['genre']} (n={gr['n']}: {who} by {_f(abs(gap)) if gap is not None else '—'}){tag}")
        big_suspects = [gr for gr in suspects if not gr["small"]]
        if big_suspects:
            beaten = [gr for gr in big_suspects if gr["engine"] is not None
                      and gr[PRIMARY] is not None and gr["engine"] > gr[PRIMARY]]
            tail = (("Of these, %d reach n>=10 (%s); the author mean beats the engine "
                     "on %d of them." % (
                         len(big_suspects),
                         ", ".join(gr["genre"] for gr in big_suspects), len(beaten)))
                    if beaten else
                    ("The only literary/Russian cell(s) at n>=10 (%s) are still won by "
                     "the engine; every other is n<10 and cannot support a conclusion." % (
                         ", ".join(gr["genre"] for gr in big_suspects))))
        else:
            tail = ("Every literary/Russian cell is n<10, so none can support a "
                    "conclusion either way — exactly the small-sample trap the rule "
                    "forbids from driving the verdict.")
        L.append("**Suspected weak spots (the brief flagged literary / Russian lit):** "
                 + "; ".join(parts) + ". " + tail + "\n")

    # 4. Mechanical verdict
    L.append("## Verdict (mechanical, from the rule above)\n")
    L.append(_table(
        ["quantity", "value"],
        [["engine honest MAE, >=3 subset", _f(d["eng3"])],
         ["author-mean MAE, >=3 subset", _f(d["auth3"])],
         ["improvement on >=3 (author − engine)", _f(d["imp3"])],
         ["engine honest MAE, overall", _f(d["engO"])],
         ["author-mean MAE, overall", _f(d["authO"])],
         ["improvement overall (author − engine)", _f(d["imp_overall"])],
         ["not worse than author-mean overall?", "yes" if d["not_worse_overall"] else "no"],
         ["loses materially (>=0.02) anywhere?", "yes" if d["loses_material"] else "no"]]))
    L.append("")
    L.append(f"**VERDICT: {verdict}.**\n")
    L.append(_verdict_paragraph(verdict, d))
    L.append("")
    return "\n".join(L) + "\n"


def _verdict_paragraph(verdict, d):
    imp3, impo = d["imp3"], d["imp_overall"]
    lead = (f"On the >=3-prior-reads subset the engine's honest WA MAE is "
            f"{d['eng3']:.3f} vs author-mean {d['auth3']:.3f} — the engine is "
            f"{'better' if imp3 > 0 else 'worse' if imp3 < 0 else 'tied'} by "
            f"{abs(imp3):.3f}. Overall the engine is "
            f"{'better' if impo > 0 else 'worse' if impo < 0 else 'tied'} by "
            f"{abs(impo):.3f}.")
    if verdict.startswith("THE ENGINE EARNS"):
        tail = ("This clears the pre-committed bar (>= 0.05 on the decision subset "
                "and not worse overall): the 14-component machinery extracts signal "
                "a naive author mean cannot.")
    elif verdict.startswith("MARGINAL"):
        tail = ("This falls in the 0.02-0.05 band: the machinery helps a little, "
                "but most of its accuracy is reproduced by simply averaging the "
                "author you already know. Treat added complexity with skepticism.")
    else:
        tail = ("The engine does not clear a 0.02 improvement on the decision "
                "subset (or loses materially somewhere), so by the pre-committed "
                "rule the machinery is not paying for itself: a metadata-only "
                "author mean matches it where author history exists. This is a "
                "standing reason to be skeptical of new engine complexity — it "
                "does not, by itself, call for simplifying the engine (out of "
                "scope here).")
    return lead + " " + tail


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build(out_dir=OUT_DIR, xlsx=None):
    rows, meta = build_eval(out_dir, xlsx)
    agg = aggregate(rows)
    return render(rows, agg, meta)


def main():
    ap = argparse.ArgumentParser(
        description="Engine ablation study: naive metadata baselines vs the "
                    "engine's honest variant on the walk-forward folds "
                    "(zero-spend, read-only, analysis-only).")
    ap.add_argument("--out-dir", default=OUT_DIR, help="validation artifact dir.")
    ap.add_argument("--xlsx", default=os.path.join(ROOT, "BookRankingsNew.xlsx"),
                    help="workbook holding the Timeline read order.")
    ap.add_argument("--check-determinism", action="store_true",
                    help="render twice and assert byte-identical output.")
    args = ap.parse_args()

    if args.check_determinism:
        a = build(args.out_dir, args.xlsx)
        b = build(args.out_dir, args.xlsx)
        ha = hashlib.sha256(a.encode()).hexdigest()
        hb = hashlib.sha256(b.encode()).hexdigest()
        print(f"render A sha256: {ha}")
        print(f"render B sha256: {hb}")
        print("DETERMINISM: PASS" if a == b else "DETERMINISM: FAIL")
        raise SystemExit(0 if a == b else 1)

    md = build(args.out_dir, args.xlsx)
    path = os.path.join(args.out_dir, REPORT_FILE)
    with open(path, "w") as fh:
        fh.write(md)
    print(md)
    print(f"  wrote {path}")


if __name__ == "__main__":
    main()
