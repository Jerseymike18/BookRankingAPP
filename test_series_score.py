"""
test_series_score.py — the series-quality model
===============================================
Guards the scoring of a SERIES as an object in its own right, rather than as a
plain mean over its books. The score is

    Avg WA + clamp(Consistency + Peak + Finale, ±budget) + Evidence

where each modifier measures something a mean is structurally blind to: whether
even the weakest volume is excellent, whether the series produced a standout, and
whether it stuck the landing. The terms live in views.series_quality_terms /
views.series_aggregate; the completeness flag that licenses the Finale term lives
in db_write.set_series_complete.

What this locks down:
  * TERM MATH: each modifier moves in the right direction, from the right raw
    quantity, and respects its own cap.
  * NO LENGTH REWARD. This is the point of the 2026-08-17 rework: the reader
    finishes every series they start, so book count measures how much they read,
    not how good it was. A long series with a bad book must score BELOW a short
    excellent one, and the retired _commitment_term must stay gone.
  * CONSISTENCY READS THE MINIMUM, against the whole library — one bad volume
    sinks it however good the rest are, and it is shrunk by n/(n+k) because two
    books are thinner evidence than ten.
  * DEVIATIONS, NOT LEVELS, for Peak and Finale: computed against the series' OWN
    average, so they add information instead of re-weighting Avg WA. Shifting a
    series' books up by a constant must leave them untouched. Consistency is the
    deliberate exception — tracking level is its whole purpose.
  * FINALE IS GATED ON COMPLETENESS. An unmarked (ongoing) series gets no Finale
    term at all — it is never charged for an ending it hasn't written. This is
    the default, so a caller that passes no series_meta can't invent endings.
  * FINALE READS SERIES ORDER, not row order or read order — it is the last
    VOLUME's Ending that matters.
  * EDGE CASES: a one-book series scores zero on every quality term and takes the
    insufficient-evidence penalty instead; its weakest percentile is None, never 0.
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


def library(*wa_values):
    """A sorted library reference (see views.library_reference) built from bare
    WA numbers, so a test can place a series' worst book at a known percentile."""
    return np.sort(np.array([float(x) for x in wa_values]))


# A flat 0..10 reference library: a book at WA w sits at roughly the w/10
# percentile, which makes the expected Consistency direction obvious by eye.
LIB = library(*[i / 10 for i in range(0, 101)])


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
    t = views.series_quality_terms(flat, library_wa=LIB)
    check("a perfectly even series gets no Peak",
          t["Peak"] == 0.0, f"peak_lift={t['Peak Lift']:.3f}")

    peaky = make_series("Peaky", [8.0, 8.0, 8.0, 9.6])
    tp = views.series_quality_terms(peaky, library_wa=LIB)
    check("a standout volume earns a positive Peak",
          tp["Peak"] > 0 and abs(tp["Peak Lift"] - (9.6 - tp_avg(peaky))) < 1e-9,
          f"peak={tp['Peak']:.3f} from lift {tp['Peak Lift']:.3f}")
    check("Peak is capped",
          views.series_quality_terms(
              make_series("Spike", [2.0, 2.0, 2.0, 10.0]),
              library_wa=LIB)["Peak"] == views._PEAK_CAP,
          f"cap={views._PEAK_CAP}")

    # ── 1b. Consistency: the weakest volume, against the whole library ───────
    print("\nSERIES QUALITY — consistency (the weakest volume)")

    strong = views.series_quality_terms(
        make_series("Strong", [9.0, 9.5, 9.2]), library_wa=LIB)
    weak = views.series_quality_terms(
        make_series("Weak", [9.0, 9.5, 2.0]), library_wa=LIB)
    check("a series whose WORST book is excellent is rewarded",
          strong["Consistency"] > 0,
          f"weakest at the {strong['Weakest Pct'] * 100:.0f}th pct "
          f"→ {strong['Consistency']:+.3f}")
    check("one bad volume sinks Consistency even with a great average elsewhere",
          weak["Consistency"] < 0,
          f"weakest at the {weak['Weakest Pct'] * 100:.0f}th pct "
          f"→ {weak['Consistency']:+.3f}")
    check("Consistency reads the MINIMUM, not the mean",
          views.series_quality_terms(
              make_series("A", [9.0, 9.0, 9.0]), library_wa=LIB)["Consistency"]
          > views.series_quality_terms(
              make_series("B", [10.0, 10.0, 7.0]), library_wa=LIB)["Consistency"],
          "B has the higher average but the weaker floor")

    # THE POINT OF THE REWORK: length must not buy anything.
    short_good = views.series_quality_terms(
        make_series("Short", [9.0, 9.1]), library_wa=LIB)
    long_same = views.series_quality_terms(
        make_series("Long", [9.0, 9.1] * 6), library_wa=LIB)
    check("a longer series with the SAME floor earns no extra reward beyond "
          "the evidence discount",
          long_same["Consistency"] > short_good["Consistency"]
          and long_same["Consistency"] <= views._CONSISTENCY_CAP + 1e-9,
          f"n=2 {short_good['Consistency']:+.3f} → n=12 "
          f"{long_same['Consistency']:+.3f} (n/(n+k) shrinkage only)")
    long_worse = views.series_quality_terms(
        make_series("LongWorse", [9.0, 9.1, 3.0] * 4), library_wa=LIB)
    check("a LONG series with a bad book scores below a SHORT excellent one",
          long_worse["Consistency"] < short_good["Consistency"],
          f"{long_worse['Consistency']:+.3f} < {short_good['Consistency']:+.3f} "
          "— the regression guard for the retired length bonus")

    # Long enough that n/(n+k) is ~1, so the raw term saturates its cap at the
    # current K. Two separate claims: the cap is never exceeded (must hold at ANY
    # K), and it actually binds at the extremes (only meaningful while the cap is
    # reachable — if K is ever cut far enough that it isn't, this is the check
    # that should fail and prompt a rethink, rather than passing vacuously).
    _best = views.series_quality_terms(
        make_series("Best", [10.0] * 200), library_wa=LIB)["Consistency"]
    _worst = views.series_quality_terms(
        make_series("Worst", [0.0] * 200), library_wa=LIB)["Consistency"]
    check("Consistency never exceeds its cap",
          max(abs(_best), abs(_worst)) <= views._CONSISTENCY_CAP + 1e-12)
    check("the cap binds at both extremes",
          _best == views._CONSISTENCY_CAP and _worst == -views._CONSISTENCY_CAP,
          f"±{views._CONSISTENCY_CAP} at K={views._CONSISTENCY_K}")
    check("thin evidence is shrunk, not trusted",
          abs(views.series_quality_terms(make_series("Two", [10.0, 10.0]),
                                         library_wa=LIB)["Consistency"])
          < abs(views.series_quality_terms(make_series("Ten", [10.0] * 10),
                                           library_wa=LIB)["Consistency"]),
          f"n/(n+{views._CONSISTENCY_SHRINK_K})")
    check("without a library reference Consistency is 0, not invented",
          views.series_quality_terms(make_series("NoRef", [9.0, 9.0]))["Consistency"] == 0.0
          and views.series_quality_terms(
              make_series("NoRef", [9.0, 9.0]))["Weakest Pct"] is None)
    check("there is NO length term left on the model",
          not hasattr(views, "_commitment_term")
          and not hasattr(views, "_LENGTH_BONUS_K"),
          "book count buys nothing — the reader finishes every series")

    # ── 2. deviations, not levels ────────────────────────────────────────────
    print("\nSERIES QUALITY — measured against the series' own average")

    shifted = make_series("Shifted", [w + 1.5 for w in (7.0, 8.0, 8.0, 9.0)],
                          endings=[7.0, 8.0, 8.0, 9.0])
    baseline = make_series("Baseline", [7.0, 8.0, 8.0, 9.0],
                           endings=[7.0, 8.0, 8.0, 9.0])
    a = views.series_quality_terms(baseline, complete=True, library_wa=LIB)
    b = views.series_quality_terms(shifted, complete=True, library_wa=LIB)
    check("lifting every book by a constant leaves Peak and Finale fixed",
          all(abs(a[k] - b[k]) < 1e-9 for k in ("Peak", "Finale")),
          "they price STRUCTURE, not level — this is why they add information "
          "rather than re-weighting Avg WA")
    check("Consistency DOES move with level, deliberately",
          b["Consistency"] > a["Consistency"],
          "it judges the weakest volume against the whole library, so tracking "
          "quality is the point of it, not a defect")

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
    ts = views.series_quality_terms(solo, complete=True, library_wa=LIB)
    check("a one-book series gets zero for every quality term",
          ts["Consistency"] == 0.0 and ts["Peak"] == 0.0 and ts["Finale"] == 0.0,
          "nothing to be consistent about, no spread, no ordering")
    check("a one-book series takes the insufficient-evidence penalty instead",
          abs(ts["Evidence"] + views._INSUFFICIENT_EVIDENCE_PENALTY) < 1e-9,
          f"evidence={ts['Evidence']:.3f}")
    check("its weakest percentile is None, never 0",
          ts["Weakest Pct"] is None,
          "0 would read as 'worse than everything', which is a different claim")

    pair = views.series_quality_terms(make_series("Pair", [8.0, 8.4]), library_wa=LIB)
    check("a two-book series IS scored, with no evidence penalty",
          pair["Peak"] > 0 and pair["Evidence"] == 0.0,
          "n>=2 is discounted smoothly by shrinkage, not by a cliff")

    missing = make_series("Missing", [8.0, 8.0, 8.0])
    missing.loc[missing.index[-1], "Ending"] = np.nan
    check("a missing Ending on the final volume suppresses the finale, not the score",
          views.series_quality_terms(missing, complete=True,
                                     library_wa=LIB)["Finale"] == 0.0)

    # ── 5. the shared clamp ──────────────────────────────────────────────────
    print("\nSERIES QUALITY — the shared budget")

    # A long run of strong volumes keeps Peak small (the max sits near the mean)
    # while the final volume craters both Consistency and Finale into their caps.
    worst = make_series("Worst", [9.0] * 20 + [0.0],
                        endings=[10.0] * 20 + [0.0])
    tw = views.series_quality_terms(worst, complete=True, library_wa=LIB)
    raw = tw["Consistency"] + tw["Peak"] + tw["Finale"]
    check("the quality terms are clamped to the shared budget",
          abs(tw["Quality"]) <= views._QUALITY_CLAMP + 1e-9,
          f"raw {raw:.3f} → clamped {tw['Quality']:.3f} "
          f"(budget +/-{views._QUALITY_CLAMP})")
    check("the clamp actually binds on an extreme series",
          abs(raw) > views._QUALITY_CLAMP)
    check("the evidence penalty sits OUTSIDE the quality budget",
          views.series_quality_terms(
              make_series("Solo2", [8.0]), library_wa=LIB)["Quality"] == 0.0,
          "it is a guard against thin data, not a judgement about the series")

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

    recon = (agg["Avg WA"] + agg["Consistency"] + agg["Peak"]
             + agg["Finale"] + agg["Evidence"])
    unclamped = (agg["Quality"]
                 - (agg["Consistency"] + agg["Peak"] + agg["Finale"])).abs() < 1e-9
    check("the per-term columns reconstruct the score exactly",
          bool(((recon - agg["Adjusted WA"]).abs()[unclamped] < 1e-9).all()),
          f"{int(unclamped.sum())} of {len(agg)} rows unclamped")
    check("series_aggregate measures Consistency against the WHOLE frame",
          agg["Weakest Pct"].notna().all()
          and bool(((agg["Weakest Pct"] >= 0) & (agg["Weakest Pct"] <= 1)).all()),
          "standalones count toward the yardstick even though they aren't ranked")

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
    check("the terms-less signature applies only the evidence guard",
          views._series_adjusted_wa(8.0, 3) == 8.0
          and abs(views._series_adjusted_wa(8.0, 1)
                  - (8.0 - views._INSUFFICIENT_EVIDENCE_PENALTY)) < 1e-9,
          "all a caller holding just a mean and a count can honestly compute")

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
              and abs(r_off["Consistency"] - r_on["Consistency"]) < 1e-12,
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
