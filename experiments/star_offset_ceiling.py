"""
star_offset_ceiling.py — go/no-go CEILING test for the Goodreads-star taste prior
================================================================================
Brief B proposes learning a per-user taste prior from Goodreads star ratings:
shrunken per-author / per-genre offsets against the reader's own star mean, fed
through the existing cold-start prior. The headline risk is that a 5-level,
ceiling-compressed star scale simply does not carry per-author/per-genre signal.

This script answers the STRICTLY EASIER version of that question, and therefore
gives an UPPER BOUND:

    Take a library where we know the fine-grained truth (the seed's 14-component
    WA), quantize it down to 1-5 stars on a Goodreads-shaped distribution, and
    ask how much of the author/genre offset structure survives the quantization.

Logic of the gate:
  * signal DIES here  -> definitive NO-GO. Real Goodreads data is strictly worse
    (rater noise, years of drift, selection bias, a reader who never rates below
    3), so it cannot beat a clean library quantized under ideal conditions.
  * signal SURVIVES   -> NECESSARY BUT NOT SUFFICIENT. Proceed to the real gate,
    which needs an actual Goodreads export and measures the things this cannot:
    true star inflation, selection bias, and TBR coverage.

Why this is an upper bound (do not read the result as the real number):
  1. WA is a 14-component composite; a real star is one noisy human judgement.
  2. The seed rates a curated, deliberately-ranged library; a real Goodreads
     shelf clusters hard at 4-5 stars.
  3. Quantization here is the ONLY noise source. Reality adds drift + bias.
  4. The seed's repeat-author structure is series-heavy, which flatters
     per-author estimates.

Zero API spend, zero writes, read-only against books.db via the read-only loader.

Run:  python3 experiments/star_offset_ceiling.py
"""

import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Shrinkage constant for the offsets (matches the engine's genre-trust constant,
# and what Brief B proposes for the star prior).
K_SHRINK = 8.0
# Minimum books before an author/genre key is even considered.
MIN_N_AUTHOR = 2
MIN_N_GENRE = 3
# A Goodreads-shaped target distribution for 1..5 stars (fractions, low->high).
# Roughly the public site-wide shape: heavily top-loaded, thin tail below 3.
GOODREADS_SHAPE = [0.03, 0.07, 0.20, 0.35, 0.35]
RNG = np.random.default_rng(0)   # deterministic bootstrap / permutation
N_BOOT = 2000
N_PERM = 2000


def quantize_to_stars(wa, shape=GOODREADS_SHAPE, noise_sd=0.0, rng=RNG):
    """Map continuous WA onto 1..5 stars so the star histogram matches `shape`.

    `noise_sd` (in WA points) is added BEFORE binning, modelling the fact that a
    real star is a noisy human judgement of the underlying enjoyment rather than
    a faithful readout of it — a reader re-rating the same book months later
    frequently lands a star away. noise_sd=0 is the noiseless ceiling and is
    close to vacuous: with a monotone, noiseless map the per-key means are almost
    a monotone transform of each other, so a high correlation there is an
    artefact of construction, not evidence. The sweep is the real test."""
    latent = np.asarray(wa, dtype=float)
    if noise_sd > 0:
        latent = latent + rng.normal(0.0, noise_sd, len(latent))
    order = np.argsort(np.argsort(latent))      # 0..n-1 rank, ties broken stably
    n = len(latent)
    cuts = np.cumsum(shape) * n
    stars = np.ones(n)
    for i, c in enumerate(cuts[:-1]):
        stars[order >= c] = i + 2
    return stars


def shrunk_offsets(keys, values, k=K_SHRINK, min_n=1):
    """{key: shrunken mean-deviation}. Exactly the estimator Brief B proposes:
    (mean(value|key) - grand_mean) * n/(n+k)."""
    grand = float(np.mean(values))
    out = {}
    for key in set(keys):
        m = np.asarray([v for kk, v in zip(keys, values) if kk == key], dtype=float)
        n = len(m)
        if n < min_n:
            continue
        out[key] = (float(m.mean()) - grand) * n / (n + k)
    return out


def _pearson(a, b):
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return _pearson(ra.astype(float), rb.astype(float))


def analyse(label, keys, wa, stars, min_n):
    wa_off = shrunk_offsets(keys, wa, min_n=min_n)
    st_off = shrunk_offsets(keys, stars, min_n=min_n)
    common = sorted(set(wa_off) & set(st_off))
    counts = {key: sum(1 for kk in keys if kk == key) for key in common}
    common = [key for key in common if counts[key] >= min_n]

    quiet = label is None          # sweep mode: compute only, print nothing
    if not quiet:
        print(f"\n{'=' * 72}\n{label}  (keys with n >= {min_n})\n{'=' * 72}")
    if len(common) < 3:
        if not quiet:
            print(f"  only {len(common)} usable key(s) — cannot assess.")
        return None

    x = np.asarray([st_off[key] for key in common])   # star-derived (star units)
    y = np.asarray([wa_off[key] for key in common])   # truth (WA units)

    r, rho = _pearson(x, y), _spearman(x, y)
    # OLS slope = the empirical "1 star ~ X WA points" conversion the brief flags
    # as its open calibration question.
    slope = float(np.polyfit(x, y, 1)[0]) if np.std(x) > 0 else float("nan")
    resid = y - (slope * x + (y.mean() - slope * x.mean()))
    recovered = 1.0 - (np.var(resid) / np.var(y)) if np.var(y) > 0 else float("nan")

    if quiet:   # sweep mode: skip the expensive resampling, return point estimates
        return {"r": r, "ci": (float("nan"),) * 2, "recovered": recovered,
                "sd_true": float(np.std(y)), "slope": slope,
                "n_keys": len(common), "p": float("nan")}

    # Bootstrap CI over keys.
    boots = []
    for _ in range(N_BOOT):
        idx = RNG.integers(0, len(common), len(common))
        if np.std(x[idx]) == 0 or np.std(y[idx]) == 0:
            continue
        boots.append(_pearson(x[idx], y[idx]))
    lo, hi = np.percentile(boots, [2.5, 97.5]) if boots else (float("nan"),) * 2

    # Permutation null: shuffle the star offsets across keys.
    null = []
    for _ in range(N_PERM):
        null.append(abs(_pearson(RNG.permutation(x), y)))
    p_perm = float(np.mean(np.asarray(null) >= abs(r))) if null else float("nan")

    print(f"  keys                       : {len(common)}")
    print(f"  SD of TRUE offsets (WA)    : {np.std(y):.4f}   <- the signal that exists")
    print(f"  SD of star offsets (stars) : {np.std(x):.4f}")
    print(f"  Pearson  r                 : {r:+.3f}   95% CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"  Spearman rho               : {rho:+.3f}")
    print(f"  permutation p (|r| by luck): {p_perm:.4f}")
    print(f"  variance recovered         : {recovered * 100:5.1f}%")
    print(f"  implied scale              : 1 star-offset ~ {slope:.3f} WA points")
    print(f"  residual SD after scaling  : {np.std(resid):.4f} WA "
          f"(vs {np.std(y):.4f} unexplained baseline)")

    worst = sorted(common, key=lambda key: abs((slope * st_off[key]) - wa_off[key]))[-3:]
    print("  largest misses             : " + ", ".join(
        f"{key} (true {wa_off[key]:+.2f} / est {slope * st_off[key]:+.2f})"
        for key in reversed(worst)))
    return {"r": r, "ci": (lo, hi), "recovered": recovered, "sd_true": float(np.std(y)),
            "slope": slope, "n_keys": len(common), "p": p_perm}


def main():
    import db_loader

    books, _gw, _gcw = db_loader.load_from_db()
    books = books.dropna(subset=["WA", "Author", "Genre"])
    wa = books["WA"].to_numpy(dtype=float)
    authors = list(books["Author"])
    genres = list(books["Genre"])
    stars = quantize_to_stars(wa)

    print("=" * 72)
    print("CEILING TEST — can 5-level stars recover author/genre taste offsets?")
    print("=" * 72)
    print(f"  library                    : {len(wa)} books, "
          f"{len(set(authors))} authors, {len(set(genres))} genres")
    print(f"  WA range                   : {wa.min():.2f} – {wa.max():.2f} "
          f"(SD {wa.std():.2f})")
    hist = {int(s): int((stars == s).sum()) for s in sorted(set(stars))}
    print(f"  star histogram             : {hist}")
    print("  NOTE: quantization is monotone and noiseless — this is the BEST case.")

    analyse("PER-AUTHOR (noiseless ceiling — near-vacuous, see docstring)",
            authors, wa, stars, MIN_N_AUTHOR)
    analyse("PER-GENRE (noiseless ceiling — near-vacuous, see docstring)",
            genres, wa, stars, MIN_N_GENRE)

    # ── The real test: how much rater noise can the offsets absorb? ──────────
    # A star bin spans roughly one SD/2 of WA here, so sigma ~ 1.0 WA is about a
    # full star of disagreement — the scale at which humans genuinely re-rate.
    print(f"\n{'=' * 72}\nNOISE SWEEP — the test that actually discriminates\n{'=' * 72}")
    print("  sigma = SD of the noise (WA points) added to the latent BEFORE binning.")
    print("  'recovered' = fraction of true offset variance explained; "
          "'usable SD' = recovered signal in WA units.\n")
    print(f"  {'sigma':>6}  {'|':1} {'author r':>9} {'recov':>7} {'usable SD':>10}"
          f"  {'|':1} {'genre r':>8} {'recov':>7} {'usable SD':>10}")
    print("  " + "-" * 74)

    sweep = {}
    for sigma in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        # Average over repeats so a single unlucky noise draw can't decide the gate.
        rows = {"author": [], "genre": []}
        for rep in range(25):
            rng = np.random.default_rng(1000 + rep)
            st = quantize_to_stars(wa, noise_sd=sigma, rng=rng)
            for name, keys, min_n in (("author", authors, MIN_N_AUTHOR),
                                      ("genre", genres, MIN_N_GENRE)):
                res = analyse(None, keys, wa, st, min_n)
                if res:
                    rows[name].append((res["r"], max(0.0, res["recovered"]),
                                       res["sd_true"]))
        agg = {}
        for name, vals in rows.items():
            if not vals:
                continue
            r = float(np.mean([v[0] for v in vals]))
            rec = float(np.mean([v[1] for v in vals]))
            usable = float(np.mean([v[2] for v in vals])) * rec ** 0.5
            agg[name] = (r, rec, usable)
        sweep[sigma] = agg
        a_r, a_rec, a_u = agg.get("author", (float("nan"),) * 3)
        g_r, g_rec, g_u = agg.get("genre", (float("nan"),) * 3)
        print(f"  {sigma:>6.2f}  | {a_r:>+9.3f} {a_rec * 100:>6.1f}% {a_u:>10.3f}"
              f"  | {g_r:>+8.3f} {g_rec * 100:>6.1f}% {g_u:>10.3f}")

    # ── Split-half: the OPERATIONAL claim, out of sample ─────────────────────
    # Everything above derives the star offset and the WA "truth" from the SAME
    # books, so they agree partly because they are two summaries of one sample.
    # The prior's actual job is different: use past books by an author to predict
    # an UNREAD one. So derive the star offset from half an author's books and
    # score it against the WA offset of the OTHER half. Underpowered by design
    # (n/2 books a side) — read the CI, not the point estimate.
    print(f"\n{'=' * 72}\nSPLIT-HALF — out-of-sample transfer (the operational claim)\n{'=' * 72}")
    for sigma in (0.0, 1.0):
        rs = []
        for rep in range(200):
            rng = np.random.default_rng(5000 + rep)
            st = quantize_to_stars(wa, noise_sd=sigma, rng=rng)
            xs, ys = [], []
            for key in sorted(set(authors)):
                idx = [i for i, a in enumerate(authors) if a == key]
                if len(idx) < 4:
                    continue
                idx = list(rng.permutation(idx))
                h = len(idx) // 2
                a_i, b_i = idx[:h], idx[h:]
                # star offset from side A, WA offset from side B (disjoint books)
                xs.append((np.mean(st[a_i]) - np.mean(st)) * len(a_i) / (len(a_i) + K_SHRINK))
                ys.append((np.mean(wa[b_i]) - np.mean(wa)) * len(b_i) / (len(b_i) + K_SHRINK))
            if len(xs) >= 3 and np.std(xs) > 0 and np.std(ys) > 0:
                rs.append(_pearson(np.asarray(xs), np.asarray(ys)))
        if not rs:
            print(f"  sigma={sigma}: too few authors with >= 4 books — INCONCLUSIVE")
            continue
        lo, hi = np.percentile(rs, [2.5, 97.5])
        n_keys = len([k for k in set(authors) if sum(1 for a in authors if a == k) >= 4])
        straddles = lo <= 0 <= hi
        print(f"  sigma={sigma:.1f} WA : r = {np.mean(rs):+.3f}  "
              f"95% CI [{lo:+.3f}, {hi:+.3f}]  ({n_keys} authors with n>=4)"
              f"{'   <- CI STRADDLES 0' if straddles else ''}")

    print(f"\n{'=' * 72}\nVERDICT\n{'=' * 72}")
    # Judge at a REALISTIC noise level, not the noiseless ceiling. Brief B caps the
    # served offset at +/-0.5 WA; an effect far under the engine's 0.636 MAE is not
    # worth the wiring, so require >= 0.10 WA of usable signal to call it material.
    JUDGE_SIGMA = 1.0
    for name in ("author", "genre"):
        r, rec, usable = sweep[JUDGE_SIGMA].get(name, (float("nan"),) * 3)
        verdict = ("PASS" if usable >= 0.10 and r >= 0.5 else
                   "WEAK" if usable >= 0.05 else "FAIL")
        print(f"  {name:6s} @ sigma={JUDGE_SIGMA} WA (~1 star of rater noise): "
              f"{verdict}  (r={r:+.3f}, recovered={rec * 100:.0f}%, "
              f"usable={usable:.3f} WA)")
    print("\n  Even a PASS is an UPPER BOUND and does NOT authorise the build: this")
    print("  library is curated and widely-ranged, its repeat-author structure is")
    print("  series-heavy, and the noise model omits selection bias and taste drift.")
    print("  The real gate still needs an actual Goodreads export.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
