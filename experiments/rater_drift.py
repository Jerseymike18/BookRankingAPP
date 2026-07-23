#!/usr/bin/env python3
"""
rater_drift.py — Phase 2.3: is the rater drifting over time?
Plots (as tables) mean actual WA by year and by month, and the model's mean
SIGNED residual (honest pred − actual) by year and month, and reports whether
there's a monotone trend and its magnitude. No modelling; read-only.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import db_loader
import db_backend

FOLDS = os.path.join(ROOT, "validation", "walkforward_folds.jsonl")
OUT = os.path.join(ROOT, "validation", "rater_drift.md")


def _slope(xs, ys):
    """Ordinary least-squares slope of ys on xs (per unit x)."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def main():
    books, _, _ = db_loader.load_from_db()
    wa = {r["Book"]: float(r["WA"]) for _, r in books.iterrows()}
    con = db_backend.connect(db_loader.DB)
    ym = {t: (y, m) for t, y, m in con.execute(
        "SELECT title, year_read, read_month FROM books WHERE user_id=?",
        (db_backend.DEFAULT_USER_ID,))}
    con.close()

    # ---- mean actual WA by year / month (ALL rated books) ----
    by_year, by_month = {}, {}
    for t, w in wa.items():
        y, m = ym.get(t, (None, None))
        if y is None:
            continue
        by_year.setdefault(y, []).append(w)
        if m is not None:
            by_month.setdefault((y, m), []).append(w)

    # ---- model signed residual (honest) by year / month (evaluated folds) ----
    res_year, res_month = {}, {}
    for line in open(FOLDS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        f = json.loads(line)
        if f.get("skip"):
            continue
        se = f["variants"]["honest"]["wa_signed_error"]
        y = f.get("year_read")
        m = ym.get(f["title"], (None, None))[1]
        if se is None or y is None:
            continue
        res_year.setdefault(y, []).append(se)
        if m is not None:
            res_month.setdefault((y, m), []).append(se)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    L = ["# Rater Drift (Phase 2.3)\n"]
    L.append("Mean **actual WA** (all rated books) and mean **model signed residual** "
             "(honest walk-forward, pred − actual) over time. Only 2 calendar years of "
             "ratings exist, so the month view (≈19 points) is the more informative trend.\n")

    L.append("## By year\n")
    L.append("| year | n | mean actual WA | n folds | mean signed residual |\n"
             "| --- | --- | --- | --- | --- |")
    for y in sorted(by_year):
        L.append(f"| {y} | {len(by_year[y])} | {_mean(by_year[y]):.3f} | "
                 f"{len(res_year.get(y, []))} | "
                 f"{'-' if not res_year.get(y) else f'{_mean(res_year[y]):+.3f}'} |")
    L.append("")

    L.append("## By month\n")
    L.append("| month | n | mean actual WA | n folds | mean signed residual |\n"
             "| --- | --- | --- | --- | --- |")
    months = sorted(set(by_month) | set(res_month))
    idx, wa_series, res_series = [], [], []
    for i, (y, m) in enumerate(months):
        wl = by_month.get((y, m), [])
        rl = res_month.get((y, m), [])
        wm = _mean(wl)
        rm = _mean(rl)
        L.append(f"| {y}-{m:02d} | {len(wl)} | {'-' if wm is None else f'{wm:.3f}'} | "
                 f"{len(rl)} | {'-' if rm is None else f'{rm:+.3f}'} |")
        if wm is not None:
            idx.append(i)
            wa_series.append(wm)
        if rm is not None:
            res_series.append((i, rm))
    L.append("")

    # ---- trend magnitudes ----
    wa_slope = _slope(idx, wa_series)
    r_idx = [i for i, _ in res_series]
    r_val = [v for _, v in res_series]
    res_slope = _slope(r_idx, r_val)
    span = (len(months) - 1) if months else 0
    L.append("## Trend\n")
    if wa_slope is not None:
        L.append(f"- **Mean actual WA**: slope **{wa_slope:+.4f} WA/month** "
                 f"(≈ {wa_slope*12:+.3f}/year; total ≈ {wa_slope*span:+.3f} over the span). "
                 f"{'Rising' if wa_slope > 0 else 'Falling' if wa_slope < 0 else 'Flat'} — "
                 "he rates a little higher over time / picks better books.")
    if res_slope is not None:
        L.append(f"- **Model signed residual**: slope **{res_slope:+.4f}/month** "
                 f"(≈ {res_slope*12:+.3f}/year). "
                 f"{'Model increasingly over-predicts' if res_slope > 0 else 'Model increasingly under-predicts' if res_slope < 0 else 'No residual drift'}.")
    yrs = sorted(by_year)
    if len(yrs) == 2:
        d = _mean(by_year[yrs[1]]) - _mean(by_year[yrs[0]])
        L.append(f"- Year-over-year mean WA change: **{d:+.3f}** "
                 f"({yrs[0]} {_mean(by_year[yrs[0]]):.2f} → {yrs[1]} {_mean(by_year[yrs[1]]):.2f}).")
    L.append("\n_Caveat: 2 calendar years is a short base; treat the slope as indicative, "
             "not a fitted drift term. Whether to add a slow time term is a Branch-A option "
             "(Phase 3), gated on this being non-trivial._\n")

    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
