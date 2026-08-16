"""
test_series_score.py — the series-quality model
===============================================
Guards the scoring of a SERIES as an object in its own right, rather than as a
plain mean over its books. The score is

    Avg WA + Commitment + Peak - Floor + Finale

where each modifier measures something a mean is structurally blind to: how long
the series sustained its quality, whether it produced a standout, whether any
volume was a slog, and whether it stuck the landing. The terms live in
views.series_quality_terms / views.series_aggregate; the completeness flag that
licenses the Finale term lives in db_write.set_series_complete.

What this locks down:
  * TERM MATH: each modifier moves in the right direction, from the right raw
    deviation, and respects its own cap.
  * THE CLAMP: the three new terms share one +/-0.75 budget, so no series can be
    carried or buried by structure alone.
  * DEVIATIONS, NOT LEVELS. Every term is computed against the series' OWN
    average. This is the whole reason the model adds information instead of
    re-weighting Avg WA, so it is asserted directly: shifting a series' books up
    by a constant must leave all three quality terms untouched.
  * FINALE IS GATED ON COMPLETENESS. An unmarked (ongoing) series gets no Finale
    term at all — it is never charged for an ending it hasn't written. This is
    the default, so a caller that passes no series_meta can't invent endings.
  * FINALE READS SERIES ORDER, not row order or read order — it is the last
    VOLUME's Ending that matters.
  * EDGE CASES: a one-book series gets zero for all three new terms (no spread to
    measure, no ordering to have a finale in) but keeps the short-series penalty.
  * AUDITABILITY: the per-term columns reconstruct the score exactly.
  * THE WRITE GATE: a typo'd or standalone series name is refused, so the table
    can't fill with orphan rows that silently never apply.
  * TENANT ISOLATION: one reader's completeness flags never reach another's.

Zero API spend — no LLM is involved. Storage checks run against a throwaway copy
of books.db.

Run:  python3 test_series_score.py     (exit 0 = pass, 1 = fail)
"""

import os
import sys
import shutil
import sqlite3
import tempfile

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_results = []


def check(name, condition, detail=""):
    _results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


CATEGORY_COMPONENTS = {
    "Story": ["Plot", "Entertainment", "Action", "Ending"],
    "Character": ["Depth", "Emotional Impact", "Motivations"],
    "Aesthetics": ["Prose", "Narration"],
    "Theme": ["Insights", "Thought-Provokingness"],
    "Worldbuilding": ["Depth2", "Integration", "Originality"],
}
ALL_COMPONENTS = [c for comps in CATEGORY_COMPONENTS.values() for c in comps]


def make_series(name, wa_values, endings=None, genre="Epic Fantasy",
                author="A. Writer", order=None):
    """A synthetic series frame. `wa_values` sets each volume's WA directly (the
    engine's WA is an input here, not something this module recomputes);
    `endings` sets the Ending component per volume. `order` overrides the series
    ordinals so row order and volume order can be made to disagree."""
    n = len(wa_values)
    nums = order if order is not None else list(range(1, n + 1))
    rows = []
    for i, wa in enumerate(wa_values):
        row = {"Book": f"{name} {i + 1}", "Series": name, "Series #": float(nums[i]),
               "Genre": genre, "Author": author, "WA": float(wa),
               "Words": 100000, "Year": 2020, "Status": "finished"}
        for c in ALL_COMPONENTS:
            row[c] = float(wa)
        if endings is not None:
            row["Ending"] = float(endings[i])
        rows.append(row)
    df = pd.DataFrame(rows)
    df.attrs["category_components"] = CATEGORY_COMPONENTS
    df.attrs["all_components"] = ALL_COMPONENTS
    return df


def main():
    import views
    import db_backend
    import db_write

    SEED = db_backend.DEFAULT_USER_ID
    USER_B = "b0000000-0000-0000-0000-0000000000bb"

    # ── 1. term math ─────────────────────────────────────────────────────────
    print("\nSERIES QUALITY — term math")

    flat = make_series("Flat", [8.0, 8.0, 8.0, 8.0])
    t = views.series_quality_terms(flat)
    check("a perfectly even series gets no Peak and no Floor",
          t["Peak"] == 0.0 and t["Floor"] == 0.0,
          f"peak_lift={t['Peak Lift']:.3f} floor_drop={t['Floor Drop']:.3f}")

    peaky = make_series("Peaky", [8.0, 8.0, 8.0, 9.6])
    tp = views.series_quality_terms(peaky)
    check("a standout volume earns a positive Peak",
          tp["Peak"] > 0 and abs(tp["Peak Lift"] - (9.6 - tp_avg(peaky))) < 1e-9,
          f"peak={tp['Peak']:.3f} from lift {tp['Peak Lift']:.3f}")

    # Floor forgives the first _FLOOR_TOL of drop.
    mild = make_series("Mild", [8.0, 8.0, 8.0, 7.8])       # drop = 0.15 < 0.40
    tm = views.series_quality_terms(mild)
    check("a small dip is forgiven — no Floor penalty inside the tolerance",
          tm["Floor"] == 0.0,
          f"floor_drop={tm['Floor Drop']:.3f} < tol {views._FLOOR_TOL}")

    dud = make_series("Dud", [8.0, 8.0, 8.0, 4.0])         # drop = 1.0 > 0.40
    td = views.series_quality_terms(dud)
    check("a genuine dud is penalised",
          td["Floor"] < 0,
          f"floor={td['Floor']:.3f} from drop {td['Floor Drop']:.3f}")

    check("Peak is capped",
          views.series_quality_terms(
              make_series("Spike", [2.0, 2.0, 2.0, 10.0]))["Peak"] == views._PEAK_CAP,
          f"cap={views._PEAK_CAP}")
    check("Floor is capped",
          views.series_quality_terms(
              make_series("Crater", [10.0, 10.0, 10.0, 0.0]))["Floor"] == -views._FLOOR_CAP,
          f"cap={views._FLOOR_CAP}")

    # ── 2. deviations, not levels ────────────────────────────────────────────
    print("\nSERIES QUALITY — measured against the series' own average")

    shifted = make_series("Shifted", [w + 1.5 for w in (7.0, 8.0, 8.0, 9.0)],
                          endings=[7.0, 8.0, 8.0, 9.0])
    baseline = make_series("Baseline", [7.0, 8.0, 8.0, 9.0],
                           endings=[7.0, 8.0, 8.0, 9.0])
    a = views.series_quality_terms(baseline, complete=True)
    b = views.series_quality_terms(shifted, complete=True)
    check("lifting every book by a constant leaves all three quality terms fixed",
          all(abs(a[k] - b[k]) < 1e-9 for k in ("Peak", "Floor", "Finale")),
          "the terms price STRUCTURE, not level — this is why they add "
          "information rather than re-weighting Avg WA")

    # ── 3. the finale term ───────────────────────────────────────────────────
    print("\nSERIES QUALITY — the finale, and the completeness gate")

    stuck = make_series("Stuck", [8.0] * 4, endings=[7.0, 7.0, 7.0, 9.0])
    botched = make_series("Botched", [8.0] * 4, endings=[9.0, 9.0, 9.0, 3.0])

    check("an ongoing series gets NO finale term (the safe default)",
          views.series_quality_terms(stuck, complete=False)["Finale"] == 0.0
          and views.series_quality_terms(botched, complete=False)["Finale"] == 0.0,
          "never charged for an ending it hasn't written")
    check("a finished series that lands it is rewarded",
          views.series_quality_terms(stuck, complete=True)["Finale"] > 0)
    check("a finished series that bottles it is penalised",
          views.series_quality_terms(botched, complete=True)["Finale"] < 0)
    check("the finale penalty bites harder than the bonus rewards",
          views._FINALE_CAP_DOWN > views._FINALE_CAP_UP,
          f"down {views._FINALE_CAP_DOWN} vs up {views._FINALE_CAP_UP}")
    check("the finale is capped in both directions",
          views.series_quality_terms(
              make_series("X", [8.0] * 3, endings=[10.0, 10.0, 0.0]),
              complete=True)["Finale"] == -views._FINALE_CAP_DOWN
          and views.series_quality_terms(
              make_series("Y", [8.0] * 3, endings=[0.0, 0.0, 10.0]),
              complete=True)["Finale"] == views._FINALE_CAP_UP)

    # Row order must not decide which book is the finale — the ordinal must.
    scrambled = make_series("Scrambled", [8.0] * 4,
                            endings=[3.0, 7.0, 7.0, 7.0],   # the BAD one is vol 4
                            order=[4, 1, 2, 3])
    check("the finale is the last VOLUME, not the last row",
          views.series_quality_terms(scrambled, complete=True)["Finale"] < 0,
          "ordinal 4 carries Ending 3.0 while sitting in row 0")

    # ── 4. edge cases ────────────────────────────────────────────────────────
    print("\nSERIES QUALITY — edge cases")

    solo = make_series("Solo", [8.5], endings=[9.0])
    ts = views.series_quality_terms(solo, complete=True)
    check("a one-book series gets zero for all three new terms",
          ts["Peak"] == 0.0 and ts["Floor"] == 0.0 and ts["Finale"] == 0.0,
          "no spread to measure, no ordering to have a finale in")
    check("a one-book series still takes the short-series penalty",
          abs(ts["Commitment"] - (-2 * views._SHORT_SERIES_PENALTY)) < 1e-9,
          f"commitment={ts['Commitment']:.3f}")

    pair = views.series_quality_terms(make_series("Pair", [8.0, 8.4]))
    check("a two-book series is scored (spread exists at n=2)",
          pair["Peak"] > 0)

    missing = make_series("Missing", [8.0, 8.0, 8.0])
    missing.loc[missing.index[-1], "Ending"] = np.nan
    check("a missing Ending on the final volume suppresses the finale, not the score",
          views.series_quality_terms(missing, complete=True)["Finale"] == 0.0)

    # ── 5. the shared clamp ──────────────────────────────────────────────────
    print("\nSERIES QUALITY — the shared budget")

    # To make the SHARED budget bind, the individual caps aren't enough: a long
    # run of strong volumes keeps Peak small (the max sits close to the mean)
    # while the final volume craters both Floor and Finale into their caps.
    worst = make_series("Worst", [9.0] * 20 + [0.0],
                        endings=[10.0] * 20 + [0.0])
    tw = views.series_quality_terms(worst, complete=True)
    raw = tw["Peak"] + tw["Floor"] + tw["Finale"]
    check("the three new terms are clamped to the shared budget",
          abs(tw["Quality"]) <= views._QUALITY_CLAMP + 1e-9,
          f"raw {raw:.3f} → clamped {tw['Quality']:.3f} "
          f"(budget +/-{views._QUALITY_CLAMP})")
    check("the clamp actually binds on an extreme series",
          abs(raw) > views._QUALITY_CLAMP)
    check("Commitment sits OUTSIDE the quality budget",
          abs(views.series_quality_terms(
              make_series("Long", [8.0] * 15))["Commitment"]) > 0.5,
          "the pre-existing length term is unchanged by the rework")

    # ── 6. aggregation + auditability ────────────────────────────────────────
    print("\nSERIES AGGREGATE — auditability and ordering")

    lib = pd.concat([
        make_series("Even", [8.0, 8.0, 8.0], endings=[8.0, 8.0, 8.0]),
        make_series("Built", [7.0, 8.0, 9.0], endings=[7.0, 7.0, 9.5]),
        make_series("Collapsed", [9.0, 8.5, 4.0], endings=[9.0, 9.0, 2.0]),
        make_series("Standalone", [7.5]),
    ], ignore_index=True)
    lib.attrs["category_components"] = CATEGORY_COMPONENTS
    lib.attrs["all_components"] = ALL_COMPONENTS

    meta = {"Even": {"complete": True}, "Built": {"complete": True},
            "Collapsed": {"complete": True}}
    agg = views.series_aggregate(lib, series_meta=meta)

    check("standalones are excluded from the series table",
          "Standalone" not in set(agg["Series"]), f"{len(agg)} series")

    recon = (agg["Avg WA"] + agg["Commitment"] + agg["Peak"]
             + agg["Floor"] + agg["Finale"])
    unclamped = (agg["Quality"] - (agg["Peak"] + agg["Floor"] + agg["Finale"])).abs() < 1e-9
    check("the per-term columns reconstruct the score exactly",
          bool(((recon - agg["Adjusted WA"]).abs()[unclamped] < 1e-9).all()),
          f"{int(unclamped.sum())} of {len(agg)} rows unclamped")

    built = agg[agg["Series"] == "Built"].iloc[0]
    collapsed = agg[agg["Series"] == "Collapsed"].iloc[0]
    check("a series that builds beats one with better books that collapses",
          built["Adjusted WA"] > collapsed["Adjusted WA"],
          f"Built {built['Adjusted WA']:.3f} (avg {built['Avg WA']:.2f}) > "
          f"Collapsed {collapsed['Adjusted WA']:.3f} (avg {collapsed['Avg WA']:.2f})")
    check("ranks follow the score, best first",
          list(agg["Rank"]) == sorted(agg["Rank"])
          and bool((agg["Adjusted WA"].diff().dropna() <= 1e-9).all()))

    # No meta at all == nothing marked complete == no finale anywhere.
    bare = views.series_aggregate(lib)
    check("series_aggregate with no meta suppresses every finale term",
          bool((bare["Finale"] == 0.0).all()) and bool((~bare["Complete"]).all()),
          "a caller that can't supply the flags never invents an ending")
    check("the legacy signature still computes the length-only adjustment",
          abs(views._series_adjusted_wa(8.0, 3)
              - (8.0 + views._commitment_term(3))) < 1e-9)

    # ── 7. storage: the write gate + tenant isolation ────────────────────────
    print("\nSERIES META — the write gate and tenant isolation")

    src = os.path.join(PROJECT_ROOT, "books.db")
    if not os.path.exists(src):
        print("books.db not present — skipping the storage checks.")
        return 1 if not all(_results) else 0

    tmpd = tempfile.mkdtemp(prefix="series_score_")
    tmpdb = os.path.join(tmpd, "books.db")
    shutil.copy2(src, tmpdb)
    orig_cwd, orig_db = os.getcwd(), db_write.DB
    try:
        os.chdir(tmpd)
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True
        db_write._series_meta_ensured = False
        db_write._ensure_series_meta()
        con = sqlite3.connect(tmpdb)
        con.execute("DELETE FROM series_meta")
        # Give USER_B a book in a series of their own, so the isolation check
        # exercises two tenants that each legitimately own a series.
        con.execute(
            "INSERT INTO books (title, genre, author, series, series_number, "
            "user_id) VALUES (?,?,?,?,?,?)",
            ("B Vol 1", "Epic Fantasy", "B Writer", "B Series", 1, USER_B))
        con.commit()
        con.close()

        real = "Malazan: Book of the Fallen"
        check("a series with no rated books is refused",
              db_write.set_series_complete("No Such Series Exists", True) is False)
        check("a standalone marker can never be marked complete",
              db_write.set_series_complete("Standalone", True) is False
              and db_write.set_series_complete("", True) is False)
        check("a real series can be marked complete",
              db_write.set_series_complete(real, True) is True
              and db_write.get_series_meta().get(real, {}).get("complete") is True)
        check("unmarking deletes the row rather than storing a zero",
              db_write.set_series_complete(real, False) is True
              and real not in db_write.get_series_meta(),
              "unmarked and explicitly-ongoing behave identically")

        db_write.set_series_complete(real, True, user_id=SEED)
        db_write.set_series_complete("B Series", True, user_id=USER_B)
        check("one tenant's completeness flags never reach another's",
              db_write.get_series_meta(user_id=SEED) == {real: {"complete": True}}
              and db_write.get_series_meta(user_id=USER_B) == {
                  "B Series": {"complete": True}})
        check("a tenant cannot mark a series they have no books in",
              db_write.set_series_complete(real, True, user_id=USER_B) is False,
              "series existence is checked inside the caller's own library")

        # End to end against the real library: the flag must change the score.
        import db_loader
        books, _, _ = db_loader.load_from_db(path=tmpdb, user_id=SEED)
        off = views.series_aggregate(books)
        on = views.series_aggregate(books, series_meta={real: {"complete": True}})
        r_off = off[off["Series"] == real].iloc[0]
        r_on = on[on["Series"] == real].iloc[0]
        check("marking a real series complete changes only its finale term",
              r_off["Finale"] == 0.0 and r_on["Finale"] != 0.0
              and abs(r_off["Peak"] - r_on["Peak"]) < 1e-12
              and abs(r_off["Floor"] - r_on["Floor"]) < 1e-12,
              f"finale {r_off['Finale']:.3f} → {r_on['Finale']:.3f}")
        check("the loader exposes the series ordinal the finale term needs",
              "Series #" in books.columns and books["Series #"].notna().any())
    finally:
        os.chdir(orig_cwd)
        db_write.DB = orig_db
        shutil.rmtree(tmpd, ignore_errors=True)

    n, total = sum(_results), len(_results)
    print("\n" + "=" * 60)
    if n == total:
        print(f"  ALL {total} CHECKS PASSED — the series model is healthy.")
    else:
        print(f"  {total - n} of {total} CHECKS FAILED.")
    print("=" * 60)
    return 0 if n == total else 1


def tp_avg(df):
    return float(df["WA"].mean())


if __name__ == "__main__":
    sys.exit(main())
