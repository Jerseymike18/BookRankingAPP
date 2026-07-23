#!/usr/bin/env python3
"""
retest_noise_floor.py — Phase 2.1: the instrument's test–retest noise floor.
============================================================================
Michael re-rates a stratified sample of books he finished 12+ months ago, BLIND
(never shown the originals). The gap between the original rating and the blind
re-rating is the irreducible noise floor of the whole instrument — the number
every later adoption decision in the accuracy roadmap is measured against.

THE RATINGS COME FROM MICHAEL, NOT FROM THIS SCRIPT. It never invents, simulates,
or infers a rating. Below the minimum it prints "insufficient data" and stops —
a fabricated floor would silently invalidate every gate in the brief.

Isolation: re-ratings are written through db_write.add_retest_rating into a
SEPARATE file (validation/retest_ratings.db), never books.db, so the prediction
path cannot read them.

WORKFLOW
--------
    python3 experiments/retest_noise_floor.py select    # pick + freeze the sample
    python3 experiments/retest_noise_floor.py rate       # blind re-rate (resumable)
    python3 experiments/retest_noise_floor.py status     # progress
    python3 experiments/retest_noise_floor.py report     # MAE + bootstrap CI (>= min rated)

SELECTION: books read Jan–Jul 2025 (12+ months before today, 2026-07), stratified
across FIVE weighted-average bands with <=3 per series so the low/mid ranges (the
high-variance, most-informative ones) are covered and favourites are not
oversampled. Deterministic (content-hash order, no RNG). The frozen selection
(validation/retest_selection.json) is SCORE-FREE and presented in a non-score
order, so nothing about a book's original rating leaks before you re-rate it.

NOTE ON WHAT IS SHOWN: title + author + genre. Genre is shown because it is
required to apply the rubric (worldbuilding is optional for realist genres) and
is NOT a prior score/component — the thing the brief says to hide. Original
component scores and WA are never shown until the report.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import db_loader
import db_backend
import db_write
import research_predict as rp

SEL_FILE = os.path.join(ROOT, "validation", "retest_selection.json")
REPORT_FILE = os.path.join(ROOT, "validation", "retest_noise_floor.md")
RATINGS_JSON = os.path.join(ROOT, "validation", "retest_ratings.json")
FOLDS_FILE = os.path.join(ROOT, "validation", "walkforward_folds.jsonl")

LIVE = db_write.FICTION_COMPONENTS
WB = db_write.WORLDBUILDING
UID = db_backend.DEFAULT_USER_ID

CATS = [("Story", ["Plot", "Entertainment", "Action", "Ending"]),
        ("Character", ["Depth", "Emotional Impact", "Motivations"]),
        ("Aesthetics", ["Prose", "Narration"]),
        ("Theme", ["Insights", "Thought-Provokingness"]),
        ("Worldbuilding", ["Depth2", "Integration", "Originality"])]

# WA bands (roughly equal-count over the eligible pool) + diversity caps. The
# eligible pool skews to a few prolific authors (Sanderson / Jordan), so caps on
# both series AND author keep the floor representative of the rating *process*
# rather than "how consistently one author is re-rated".
STRATA = [(0.0, 6.5), (6.5, 7.3), (7.3, 8.0), (8.0, 8.6), (8.6, 1e9)]
PER_STRATUM = 6
SERIES_CAP = 3
AUTHOR_CAP = 4
SEED = "retest-2026-07-23"
MIN_FOR_REPORT = 25


# ---------------------------------------------------------------------------
def _hkey(tag, title):
    return hashlib.sha256(f"{SEED}:{tag}:{title}".encode()).hexdigest()


def _load_pool():
    """Eligible books (read Jan–Jul 2025 = 12+ months ago) with WA + metadata."""
    books, gw, gcw = db_loader.load_from_db()
    con = db_backend.connect(db_loader.DB)
    meta = {t: (y, m, s) for t, y, m, s in con.execute(
        "SELECT title, year_read, read_month, series FROM books WHERE user_id=?", (UID,))}
    con.close()
    pool = []
    for _, r in books.iterrows():
        y, m, series = meta.get(r["Book"], (None, None, None))
        if y == 2025 and m is not None and 1 <= m <= 7:
            pool.append({"title": r["Book"], "author": r["Author"], "genre": r["Genre"],
                         "series": (series or "").strip(), "wa": float(r["WA"])})
    return pool, gw, gcw


def _stratify(pool):
    chosen, series_count, author_count = [], {}, {}
    for lo, hi in STRATA:
        bucket = sorted([b for b in pool if lo <= b["wa"] < hi],
                        key=lambda b: _hkey("pick", b["title"]))
        picked = 0
        for b in bucket:
            if picked >= PER_STRATUM:
                break
            s = b["series"]
            real = bool(s) and s.lower() != "standalone"
            if real and series_count.get(s, 0) >= SERIES_CAP:
                continue
            if author_count.get(b["author"], 0) >= AUTHOR_CAP:
                continue
            chosen.append(b)
            picked += 1
            author_count[b["author"]] = author_count.get(b["author"], 0) + 1
            if real:
                series_count[s] = series_count.get(s, 0) + 1
    return chosen


def _print_list(sel):
    for i, b in enumerate(sel, 1):
        print(f"  {i:>2}. {b['title'][:40]:<40} — {b['author'][:22]:<22} ({b['genre']})")


def _mirror_json():
    """Durable, git-committable mirror of the re-ratings (the isolated .db is
    gitignored). Written after every save so a mid-collection crash loses nothing."""
    data = db_write.get_retest_ratings(UID)
    json.dump(data, open(RATINGS_JSON, "w", encoding="utf-8"),
              indent=2, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
def cmd_select(args):
    if os.path.exists(SEL_FILE) and not args.force:
        sel = json.load(open(SEL_FILE, encoding="utf-8"))
        print(f"Selection already frozen ({len(sel)} books): {SEL_FILE}")
        print("(use --force to regenerate — only before any re-rating.)\n")
        _print_list(sel)
        return
    pool, _, _ = _load_pool()
    chosen = _stratify(pool)
    order = sorted(chosen, key=lambda b: _hkey("order", b["title"]))   # non-score order
    sel = [{"title": b["title"], "author": b["author"], "genre": b["genre"]} for b in order]
    os.makedirs(os.path.dirname(SEL_FILE), exist_ok=True)
    json.dump(sel, open(SEL_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"Eligible pool: {len(pool)} books (read Jan–Jul 2025). "
          f"Selected {len(sel)} (stratified by WA, <=3/series).\n")
    _print_list(sel)
    print(f"\nWrote {SEL_FILE}  (score-free)."
          f"\nNext: python3 experiments/retest_noise_floor.py rate")


def _prompt_cat(cat, comps):
    wb = cat == "Worldbuilding"
    tag = " (blank = N/A)" if wb else ""
    while True:
        raw = input(f"    {cat:<13}[{' '.join(comps)}]{tag}: ").strip()
        low = raw.lower()
        if low in ("q", "quit"):
            return "QUIT"
        if low in ("s", "skip"):
            return "SKIP"
        if wb and raw == "":
            return {c: None for c in comps}
        parts = raw.replace(",", " ").split()
        if len(parts) != len(comps):
            print(f"      need {len(comps)} numbers (got {len(parts)}). Try again.")
            continue
        try:
            vals = [float(x) for x in parts]
        except ValueError:
            print("      non-numeric. Try again.")
            continue
        if any(not (0 <= v <= 10) for v in vals):
            print("      scores must be 0–10. Try again.")
            continue
        return dict(zip(comps, vals))


def cmd_rate(args):
    if not os.path.exists(SEL_FILE):
        print("No selection yet. Run: python3 experiments/retest_noise_floor.py select")
        return
    sel = json.load(open(SEL_FILE, encoding="utf-8"))
    done = db_write.get_retest_ratings(UID)
    todo = [b for b in sel if b["title"] not in done]
    if not todo:
        print(f"All {len(sel)} selected books already re-rated. Run `report`.")
        return
    print(f"Blind re-rating — {len(done)}/{len(sel)} done, {len(todo)} to go.")
    print("Type each category's scores space-separated (0–10). Worldbuilding may be")
    print("left blank for realist genres. 's' = skip this book, 'q' = save & quit.\n")
    for b in todo:
        idx = sel.index(b) + 1
        print("=" * 62)
        print(f"[{idx}/{len(sel)}]  {b['title']}  —  {b['author']}   ({b['genre']})")
        scores, control = {}, None
        for cat, comps in CATS:
            res = _prompt_cat(cat, comps)
            if res == "QUIT":
                print("\nProgress saved. Resume any time with `rate`.")
                return
            if res == "SKIP":
                control = "SKIP"
                break
            scores.update(res)
        if control == "SKIP":
            print("  (skipped — will reappear next `rate`)\n")
            continue
        if db_write.add_retest_rating(b["title"], scores, UID):
            done[b["title"]] = scores
            _mirror_json()
            print(f"  ✓ saved  ({len(done)}/{len(sel)})\n")
        else:
            print("  ✗ not saved (validation failed) — will reappear next `rate`\n")
    print(f"\nAll caught up ({len(done)}/{len(sel)}). "
          f"Run `report` once >= {min(MIN_FOR_REPORT, len(sel))} are rated.")


def cmd_status(args):
    if not os.path.exists(SEL_FILE):
        print("No selection yet. Run `select`.")
        return
    sel = json.load(open(SEL_FILE, encoding="utf-8"))
    done = db_write.get_retest_ratings(UID)
    have = [b for b in sel if b["title"] in done]
    print(f"Re-rated: {len(have)}/{len(sel)} "
          f"(minimum for report: {min(MIN_FOR_REPORT, len(sel))}).")
    remaining = [b for b in sel if b["title"] not in done]
    if remaining:
        print("Remaining:")
        _print_list(remaining)


# ---------------------------------------------------------------------------
def _bootstrap_ci(errs, B=10000):
    import random
    rng = random.Random(SEED)
    n = len(errs)
    means = []
    for _ in range(B):
        s = 0.0
        for _ in range(n):
            s += errs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return means[int(0.025 * B)], means[int(0.975 * B)]


def _walkforward_honest_mae():
    """Committed time-mode honest WA MAE, for the headroom comparison."""
    if not os.path.exists(FOLDS_FILE):
        return None
    errs = []
    for line in open(FOLDS_FILE, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("skip"):
            continue
        e = rec["variants"]["honest"]["wa_abs_error"]
        if e is not None:
            errs.append(e)
    return (sum(errs) / len(errs)) if errs else None


def cmd_report(args):
    if not os.path.exists(SEL_FILE):
        print("No selection yet. Run `select`.")
        return
    sel = json.load(open(SEL_FILE, encoding="utf-8"))
    retest = db_write.get_retest_ratings(UID)
    rated = [b for b in sel if b["title"] in retest]
    need = min(MIN_FOR_REPORT, len(sel))
    if len(rated) < need:
        print(f"insufficient data: {len(rated)}/{need} re-rated.\n"
              "The noise floor is NOT computed (and never fabricated) until Michael has\n"
              "entered enough blind re-ratings. Run `rate`.")
        return

    books, gw, gcw = db_loader.load_from_db()
    orig = {r["Book"]: r for _, r in books.iterrows()}

    rows, wa_errs = [], []
    comp_abs = {c: [] for c in LIVE}
    for b in rated:
        t = b["title"]
        o = orig.get(t)
        if o is None:
            continue
        rt = {c: retest[t].get(c) for c in LIVE}
        wa_o = float(o["WA"])
        wa_r = rp._wa_from_components(rt, b["genre"], gw, gcw)
        wa_errs.append(abs(wa_o - wa_r))
        rows.append({"title": t, "genre": b["genre"], "wa_o": wa_o, "wa_r": wa_r,
                     "d": wa_r - wa_o})
        for c in LIVE:
            ov, rv = o[c], rt.get(c)
            if ov is None or rv is None:
                continue
            if c in WB and (ov in (0, 0.0)):     # realist sentinel — engine excludes it too
                continue
            comp_abs[c].append(abs(float(ov) - float(rv)))

    n = len(wa_errs)
    wa_mae = sum(wa_errs) / n
    lo, hi = _bootstrap_ci(wa_errs)
    wf = _walkforward_honest_mae()

    L = ["# Test–Retest Noise Floor (Phase 2.1)\n"]
    L.append(f"Blind re-ratings by Michael of {n} books finished 12+ months ago "
             "(read Jan–Jul 2025), stratified across the WA range. The originals were "
             "hidden during re-rating; this compares them.\n")
    L.append("## Headline — WA test–retest MAE\n")
    L.append(f"**{wa_mae:.3f} WA** (95% bootstrap CI [{lo:.3f}, {hi:.3f}], n={n}).\n")
    if wf is not None:
        L.append("## Headroom vs the engine (Phase-1 walk-forward, honest)\n")
        L.append("| | WA MAE |\n| --- | --- |")
        L.append(f"| test–retest noise floor (this) | {wa_mae:.3f} |")
        L.append(f"| engine — time split | {wf:.3f} |")
        L.append(f"| engine — author-holdout (cold-start) | 0.859 |")
        L.append(f"| engine — series-holdout | 0.817 |")
        L.append("")
        L.append("_Gate A reading (owner's call): if the floor sits at/above the grouped "
                 "cold-start MAE (~0.82–0.86), point accuracy is at its irreducible limit — "
                 "skip Phases 3–4, go to Phase 5. If the floor is well below it, there is "
                 "real headroom and Gate B selects the branch._\n")
    # component MAE, worst first
    L.append("## Component test–retest MAE (worst first; WB realist sentinel excluded)\n")
    L.append("| component | n | MAE |\n| --- | --- | --- |")
    crows = [(c, len(comp_abs[c]), (sum(comp_abs[c]) / len(comp_abs[c])) if comp_abs[c] else None)
             for c in LIVE]
    for c, cn, cm in sorted(crows, key=lambda r: (r[2] is None, -(r[2] or 0))):
        L.append(f"| {c}{' *(WB)*' if c in WB else ''} | {cn} | "
                 f"{'-' if cm is None else f'{cm:.3f}'} |")
    L.append("")
    # per-book
    L.append("## Per-book (original vs blind re-rating WA)\n")
    L.append("| title | genre | orig WA | retest WA | Δ |\n| --- | --- | --- | --- | --- |")
    for r in sorted(rows, key=lambda r: -abs(r["d"])):
        L.append(f"| {r['title'][:34]} | {r['genre'][:20]} | {r['wa_o']:.2f} | "
                 f"{r['wa_r']:.2f} | {r['d']:+.2f} |")
    L.append("")
    md = "\n".join(L) + "\n"
    open(REPORT_FILE, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {REPORT_FILE}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Phase 2.1 test–retest noise-floor tool.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("select", help="pick + freeze the stratified sample.")
    ps.add_argument("--force", action="store_true", help="regenerate even if one exists.")
    sub.add_parser("rate", help="blind re-rate the selected books (resumable).")
    sub.add_parser("status", help="show progress.")
    sub.add_parser("report", help="compute MAE + bootstrap CI (>= min rated).")
    args = ap.parse_args()
    {"select": cmd_select, "rate": cmd_rate, "status": cmd_status,
     "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
