"""
residual_bias_diagnostic.py
============================
Read-only diagnostic: does the LIVE author_genre correction
(reresearch_and_measure.correct_book) leave systematic per-component bias
uncorrected? No writes, no engine changes — this only reads books.db and
llm_scores_richer.json and reports numbers.

BACKGROUND: an earlier capstone brief assumed a frozen, hand-set
per-component correction dict existed somewhere the engine reads at 15%
weight ("RatingGuidelines 6C"). That dict does not exist in code; Section 6
of RatingGuidelines is a manual, read-live spreadsheet protocol, not a
constant table. The only correction actually wired into the live engine is
correct_book(method="author_genre") — a hierarchical genre/author deviation
correction, shrinkage-weighted by K_GENRE=6.0 / K_AUTHOR=4.0. This script
measures whether THAT correction still leaves per-component bias behind, to
decide whether delta_log's 126 rows should inform (A) retuning it or
(B) a new additive layer on top — or neither.

DATA SOURCE: llm_scores_richer.json (raw LLM component scores) joined
against the DB books table (actual component scores) via
reresearch_and_measure.build_pairs — the exact same data source and join
correct_and_predict uses in production (backend/main.py -> research_predict
-> correct_and_predict). delta_log's pred_*/act_* columns are a DIFFERENT
signal (historical TBRFinished spreadsheet predictions, produced by a manual
research workflow that predates and does not use correct_book) and are not
substitutable here.

WORLDBUILDING CAVEAT: the books table stores 0.0 (not NULL) for realist
genres with no worldbuilding (CLAUDE.md: "Worldbuilding is optional (0) for
realist genres"). The LLM is blind to that convention and scores WB
components normally even for those books, so a naive residual would include
a spurious ~-8 point "error" per realist book. Books where all three WB
actuals are exactly 0.0 are excluded from the WB component means (matches
the "exclude worldbuilding NULLs" instruction, adapted for this schema's
0-as-N/A convention).

HOW TO RUN
    python3 residual_bias_diagnostic.py
"""

import json
import numpy as np
import predict_engine as pe
import reresearch_and_measure as rm
import db_loader

LIVE = rm.LIVE
WB = ["Depth2", "Integration", "Originality"]
SIG_HIGH = 2.0   # |mean residual| / SE >= this -> residual bias remains
SIG_LOW = 1.0    # below this -> already corrected; between -> borderline


def wa_from_components(scores, genre, gw, gcw):
    wa = 0.0
    for cat in db_loader.CATEGORY_OF_INTEREST:
        wcat = db_loader._weighted_cat_avg(scores, genre, cat, gcw)
        wa += wcat * (gw.get(genre, {}).get(cat, 0) or 0)
    return wa


def main():
    books, gw, gcw = pe.build(source="db")[:3]
    cache = json.load(open("llm_scores_richer.json"))
    df = rm.build_pairs(books, cache)
    print(f"Eval set (llm cache ∩ DB actuals, all 14 components present): n={len(df)}")

    has_wb = ~((df["you_Depth2"] == 0) & (df["you_Integration"] == 0)
               & (df["you_Originality"] == 0))
    print(f"Books with genuine worldbuilding: {has_wb.sum()} / {len(df)}  "
          f"(excluded {(~has_wb).sum()} realist-genre 0-sentinel rows from WB means)\n")

    raw_res = {c: [] for c in LIVE}
    ag_res = {c: [] for c in LIVE}
    wa_raw_err, wa_ag_err = [], []

    for i in range(len(df)):
        b = df.iloc[i]
        raw_pred = rm.correct_book(df, i, "raw")
        ag_pred = rm.correct_book(df, i, "author_genre")
        for c in LIVE:
            if c in WB and not has_wb.iloc[i]:
                continue
            actual = b["you_" + c]
            raw_res[c].append(actual - raw_pred[c])
            ag_res[c].append(actual - ag_pred[c])
        genre = b["Genre"]
        actual_scores = {c: b["you_" + c] for c in LIVE}
        wa_actual = wa_from_components(actual_scores, genre, gw, gcw)
        wa_raw_err.append(abs(wa_actual - wa_from_components(raw_pred, genre, gw, gcw)))
        wa_ag_err.append(abs(wa_actual - wa_from_components(ag_pred, genre, gw, gcw)))

    print(f"Overall WA MAE   raw: {np.mean(wa_raw_err):.4f}   "
          f"author_genre: {np.mean(wa_ag_err):.4f}\n")

    print(f"{'Component':24}{'raw mean':>10}{'ag mean':>10}{'ag std':>10}"
          f"{'n':>6}{'SE':>8}{'|ag|/SE':>10}  verdict")
    for c in LIVE:
        r = np.array(raw_res[c])
        a = np.array(ag_res[c])
        n = len(a)
        se = a.std(ddof=1) / np.sqrt(n)
        ratio = abs(a.mean()) / se if se > 0 else float("inf")
        if ratio >= SIG_HIGH:
            verdict = "residual bias remains"
        elif ratio < SIG_LOW:
            verdict = "already corrected"
        else:
            verdict = "borderline"
        print(f"{c:24}{r.mean():>10.3f}{a.mean():>10.3f}{a.std(ddof=1):>10.3f}"
              f"{n:>6}{se:>8.3f}{ratio:>10.2f}  {verdict}")


if __name__ == "__main__":
    main()
