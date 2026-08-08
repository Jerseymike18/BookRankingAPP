"""
test_star_genre_prior.py — star-derived genre prior regression
==============================================================
Guards the Workstream-B genre prior: shrunken per-genre offsets computed from a
reader's imported Goodreads star ratings, filling the SAME genre_prior slot that
self-reported favorite genres fill today.

What this locks down:
  * DEFAULT IS BYTE-IDENTICAL — with no stored offsets, _build_genre_prior returns
    exactly the favorites object it always did. This is the regression guard: every
    existing tenant, the walk-forward baseline, and test_engine are untouched.
  * the shrinkage formula, and that unrated (0) / out-of-range stars never average in
  * SIGNED behaviour — a below-average genre yields a NEGATIVE offset and LOWERS a
    cold-slice prediction (the substantive difference from the favorites prior, and
    the riskiest part of the change)
  * the +/- cap, so a coarse 5-level signal can't outweigh the prior it replaces
  * REPLACE-not-stack: star offsets supersede favorites, never add to them
  * the kill switch falls back to favorites
  * the per-genre fade is unchanged: n_genre > 0 => no adjustment at all
  * engine_parameters reports the right provenance

Zero API, zero writes, no DB needed — these are pure functions.

Run:  python3 test_star_genre_prior.py     (exit 0 = pass, 1 = fail)
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_results = []


def check(name, condition, detail=""):
    _results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(condition)


def rows(*specs):
    """(genre, star) pairs -> staging-row dicts."""
    return [{"genre": g, "goodreads_rating": s} for g, s in specs]


def main():
    import star_priors as sp
    import research_predict as rp
    import engine_parameters as ep
    import backend.main as bm

    print("\nSTAR GENRE PRIOR — offset computation")
    # 4 Epic Fantasy at 5 (well above), 4 Literary at 1 (well below), grand mean 3.
    off = sp.genre_offsets(rows(*[("Epic Fantasy", 5)] * 4, *[("Literary Fiction", 1)] * 4))
    check("both genres get an offset", set(off) == {"Epic Fantasy", "Literary Fiction"},
          f"{sorted(off)}")
    check("above-average genre is POSITIVE", off["Epic Fantasy"] > 0,
          f"{off['Epic Fantasy']:+.4f}")
    check("below-average genre is NEGATIVE", off["Literary Fiction"] < 0,
          f"{off['Literary Fiction']:+.4f}")
    expect = (5 - 3) * 4 / (4 + sp.K_STAR_GENRE)
    check("shrinkage matches (mean-grand)*n/(n+k)",
          abs(off["Epic Fantasy"] - expect) < 1e-12, f"{off['Epic Fantasy']:.6f} == {expect:.6f}")

    thin = sp.genre_offsets(rows(("Epic Fantasy", 5), ("Epic Fantasy", 5),
                                 *[("Literary Fiction", 1)] * 4))
    check("a genre under MIN_N_GENRE gets no offset", "Epic Fantasy" not in thin,
          f"n=2 < {sp.MIN_N_GENRE}; got {sorted(thin)}")

    unrated = sp.genre_offsets(rows(*[("Epic Fantasy", 5)] * 4, *[("Literary Fiction", 1)] * 4,
                                    *[("Epic Fantasy", 0)] * 3))
    check("unrated (0 stars) rows are ignored, not averaged as zero",
          abs(unrated["Epic Fantasy"] - off["Epic Fantasy"]) < 1e-12,
          "identical to the run without them")
    junk = sp.genre_offsets(rows(*[("Epic Fantasy", 5)] * 4, *[("Literary Fiction", 1)] * 4,
                                 ("Epic Fantasy", 9), ("Epic Fantasy", None)))
    check("out-of-range / missing ratings are ignored",
          abs(junk["Epic Fantasy"] - off["Epic Fantasy"]) < 1e-12, "identical")
    check("no usable rows -> empty (callers fall back)", sp.genre_offsets([]) == {}, "{}")

    print("\nSTAR GENRE PRIOR — shaping into the genre_prior slot")
    gp = sp.to_genre_prior(off, rp.normalize_genre)
    check("emits base=1.0 with WA offsets folded into the map",
          gp["base"] == 1.0, f"base={gp['base']}")
    check("map carries a negative weight for the disliked genre",
          gp["map"][rp.normalize_genre("Literary Fiction")] < 0,
          f"{gp['map'][rp.normalize_genre('Literary Fiction')]:+.4f}")
    huge = sp.to_genre_prior({"Epic Fantasy": 99.0, "Literary Fiction": -99.0},
                             rp.normalize_genre)
    vals = list(huge["map"].values())
    check("cap bounds the offset in BOTH directions",
          max(vals) == sp.STAR_GENRE_CAP and min(vals) == -sp.STAR_GENRE_CAP,
          f"{min(vals):+.2f} .. {max(vals):+.2f} (cap {sp.STAR_GENRE_CAP})")
    check("empty offsets -> None (so the caller falls back)",
          sp.to_genre_prior({}, rp.normalize_genre) is None, "None")

    print("\nSTAR GENRE PRIOR — _build_genre_prior selection")
    favs = ["Epic Fantasy", "Cyberpunk"]
    base_favs = bm._build_genre_prior(favs)
    check("DEFAULT (no offsets) is byte-identical to the favorites prior",
          bm._build_genre_prior(favs, None) == base_favs
          and bm._build_genre_prior(favs, {}) == base_favs,
          f"base={base_favs['base']}, {len(base_favs['map'])} genre(s)")
    check("favorites prior is still positive-only (unchanged behaviour)",
          all(w == 1.0 for w in base_favs["map"].values()) and base_favs["base"] > 0,
          "uniform 1.0 weights")

    starred = bm._build_genre_prior(favs, off)
    check("with offsets, the STAR prior is used instead", starred != base_favs, "differs")
    check("REPLACE not stack: Cyberpunk (a favorite, unrated) is absent",
          rp.normalize_genre("Cyberpunk") not in starred["map"],
          f"map keys = {sorted(starred['map'])}")

    orig = bm.STAR_GENRE_PRIOR_ENABLED
    try:
        bm.STAR_GENRE_PRIOR_ENABLED = False
        check("kill switch falls back to the favorites prior",
              bm._build_genre_prior(favs, off) == base_favs, "STAR_GENRE_PRIOR=0")
    finally:
        bm.STAR_GENRE_PRIOR_ENABLED = orig

    print("\nSTAR GENRE PRIOR — applied through the engine's cold-start term")
    term = {"genre_prior": starred}
    lit, fan = "Literary Fiction", "Epic Fantasy"
    up = rp.apply_cold_start_term(7.0, None, None, "A", fan, 1, 0, term)
    down = rp.apply_cold_start_term(7.0, None, None, "A", lit, 1, 0, term)
    check("liked genre raises a cold-slice prediction", up > 7.0, f"7.00 -> {up:.4f}")
    check("disliked genre LOWERS it (the signed change)", down < 7.0, f"7.00 -> {down:.4f}")
    check("fade is unchanged: n_genre > 0 -> no adjustment at all",
          rp.apply_cold_start_term(7.0, None, None, "A", lit, 1, 3, term) == 7.0,
          "exactly 7.0")
    check("adjusted WA stays clamped to [0, 10]",
          rp.apply_cold_start_term(0.1, None, None, "A", lit, 1, 0, term) >= 0.0
          and rp.apply_cold_start_term(9.95, None, None, "A", fan, 1, 0, term) <= 10.0,
          "both ends")
    check("term with no genre_prior is a no-op",
          rp.apply_cold_start_term(7.0, None, None, "A", fan, 1, 0, {}) == 7.0, "7.0")

    print("\nSTAR GENRE PRIOR — engine-parameters provenance")
    check("source 'stars' for a star prior",
          ep._genre_prior_source({"genre_prior": starred}) == "stars", "stars")
    check("source 'favorites' for the favorites prior",
          ep._genre_prior_source({"genre_prior": base_favs}) == "favorites", "favorites")
    check("source 'off' when no prior applies",
          ep._genre_prior_source(None) == "off"
          and ep._genre_prior_source({}) == "off", "off")

    total, passed = len(_results), sum(_results)
    print("\n" + "=" * 60)
    if passed == total:
        print(f"  ALL {total} CHECKS PASSED — star genre prior is sound.")
    else:
        print(f"  {passed}/{total} passed — {total - passed} FAILED.")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
