"""
star_offset_real.py — Brief B gate, run against a REAL Goodreads export
=======================================================================
Companion to star_offset_ceiling.py (which used quantized WA as a stand-in).
This joins an actual Goodreads export to the reader's own 14-component library
and asks the operational question directly:

    Do the reader's REAL 1-5 star ratings recover the same per-author /
    per-genre taste offsets that their fine-grained WA does?

READ THE PROVENANCE BLOCK BEFORE THE NUMBERS. This gate is only meaningful if
the stars were assigned INDEPENDENTLY of the app. If they were transcribed from
the app's own tier list / WA ranking, every correlation below is circular and
proves nothing. The script prints the evidence it can see (bulk-entry dates, a
suspiciously uniform rating spread, book-level agreement) but it CANNOT settle
this on its own — ask the owner.

Also note what a given export can and cannot answer:
  * TBR coverage      — needs `to-read` rows. An export with none cannot answer
                        what fraction of a reader's to-read list would even get
                        an offset, which is the risk that can sink the feature
                        independently of any correlation.
  * star inflation    — needs an organically-rated shelf. A deliberately
                        full-range ranking cannot show the 4-5 star clustering
                        that makes real Goodreads data weak.
  * selection bias    — not addressable from an export alone.

Zero API, zero writes.

Run:  python3 experiments/star_offset_real.py "/path/to/goodreads_library_export.csv"
"""

import collections
import csv
import io
import os
import re
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

K_SHRINK = 8.0
MIN_N_AUTHOR = 2
MIN_N_GENRE = 3
RNG = np.random.default_rng(0)
N_BOOT = 2000

# A typical site-wide Goodreads shape, for comparison against the export's own.
TYPICAL_GOODREADS = {1: 0.03, 2: 0.07, 3: 0.20, 4: 0.35, 5: 0.35}


def norm_title(t):
    """Strip a trailing '(Series, #N)' tag and punctuation for joining."""
    t = re.sub(r"\s*\([^()]*#[\d.]+\)\s*$", "", t or "")
    t = re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
    return re.sub(r"\s+", " ", t)


def read_export(path):
    """Parse My Rating ROBUSTLY.

    NOTE: the shipped goodreads_import._to_int uses int(), which returns None for
    a '4.0'-style cell — the format you get whenever an export has been opened
    and re-saved in Excel / Numbers / Sheets. That silently drops every rating on
    import. This reads via float() so the gate is not blocked by that bug."""
    raw = open(path, encoding="utf-8-sig").read()
    out = []
    for r in csv.DictReader(io.StringIO(raw)):
        try:
            star = float(r.get("My Rating") or 0)
        except (TypeError, ValueError):
            star = 0.0
        out.append({
            "title": r.get("Title", ""),
            "author": (r.get("Author") or "").strip(),
            "shelf": (r.get("Exclusive Shelf") or "").strip(),
            "star": star if star > 0 else None,
            "date_read": (r.get("Date Read") or "").strip(),
            "date_added": (r.get("Date Added") or "").strip(),
            "review": (r.get("My Review") or "").strip(),
        })
    return out


def shrunk_offsets(keys, values, k=K_SHRINK, min_n=1):
    grand = float(np.mean(values))
    out = {}
    for key in set(keys):
        m = np.asarray([v for kk, v in zip(keys, values) if kk == key], dtype=float)
        if len(m) < min_n:
            continue
        out[key] = (float(m.mean()) - grand) * len(m) / (len(m) + k)
    return out


def _pearson(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a, b):
    return _pearson(np.argsort(np.argsort(a)), np.argsort(np.argsort(b)))


def offset_agreement(label, keys, wa, stars, min_n):
    wa_off = shrunk_offsets(keys, wa, min_n=min_n)
    st_off = shrunk_offsets(keys, stars, min_n=min_n)
    counts = collections.Counter(keys)
    common = sorted(k for k in (set(wa_off) & set(st_off)) if counts[k] >= min_n)
    print(f"\n{'-' * 72}\n{label}  (keys with n >= {min_n})\n{'-' * 72}")
    if len(common) < 3:
        print(f"  only {len(common)} usable key(s) — INCONCLUSIVE")
        return None
    x = np.asarray([st_off[k] for k in common])
    y = np.asarray([wa_off[k] for k in common])
    r = _pearson(x, y)
    slope = float(np.polyfit(x, y, 1)[0])
    resid = y - (slope * x + (y.mean() - slope * x.mean()))
    recovered = 1.0 - np.var(resid) / np.var(y)
    boots = [_pearson(x[i], y[i]) for i in
             (RNG.integers(0, len(common), len(common)) for _ in range(N_BOOT))]
    boots = [b for b in boots if not np.isnan(b)]
    lo, hi = np.percentile(boots, [2.5, 97.5]) if boots else (float("nan"),) * 2
    print(f"  keys                    : {len(common)}")
    print(f"  SD of TRUE offsets (WA) : {np.std(y):.4f}")
    print(f"  Pearson r               : {r:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"  Spearman rho            : {_spearman(x, y):+.3f}")
    print(f"  variance recovered      : {recovered * 100:5.1f}%")
    print(f"  implied scale           : 1 star-offset ~ {slope:.3f} WA points")
    return {"r": r, "ci": (lo, hi), "recovered": recovered,
            "sd_true": float(np.std(y)), "n_keys": len(common)}


def split_half(authors, wa, stars, rng_seed=5000, reps=400, min_n=4):
    """Star offset from half an author's books vs WA offset of the OTHER half —
    the out-of-sample claim the prior actually makes."""
    counts = collections.Counter(authors)
    eligible = [a for a, c in counts.items() if c >= min_n]
    if len(eligible) < 3:
        return None, len(eligible)
    rs = []
    for rep in range(reps):
        rng = np.random.default_rng(rng_seed + rep)
        xs, ys = [], []
        for key in eligible:
            idx = list(rng.permutation([i for i, a in enumerate(authors) if a == key]))
            h = len(idx) // 2
            a_i, b_i = idx[:h], idx[h:]
            xs.append((np.mean(stars[a_i]) - np.mean(stars)) * len(a_i) / (len(a_i) + K_SHRINK))
            ys.append((np.mean(wa[b_i]) - np.mean(wa)) * len(b_i) / (len(b_i) + K_SHRINK))
        r = _pearson(xs, ys)
        if not np.isnan(r):
            rs.append(r)
    return rs, len(eligible)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    import db_loader

    rows = read_export(path)
    books, _gw, _gcw = db_loader.load_from_db()
    books = books.dropna(subset=["WA", "Author", "Genre"])
    lib = {norm_title(t): (float(w), str(a), str(g)) for t, w, a, g
           in zip(books["Book"], books["WA"], books["Author"], books["Genre"])}

    print("=" * 72)
    print("REAL-EXPORT GATE — Goodreads stars vs the reader's own WA")
    print("=" * 72)

    # ── Provenance: is this export usable as evidence at all? ───────────────
    print("\nPROVENANCE (read this before any correlation below)")
    shelves = collections.Counter(r["shelf"] for r in rows)
    rated = [r for r in rows if r["star"]]
    dist = collections.Counter(int(r["star"]) for r in rated)
    n = len(rated)
    print(f"  rows / rated            : {len(rows)} / {n}")
    print(f"  shelves                 : {dict(shelves)}")
    tbr = shelves.get("to-read", 0) + shelves.get("currently-reading", 0)
    print(f"  to-read + currently     : {tbr}"
          + ("   <- ZERO: TBR COVERAGE IS UNANSWERABLE" if tbr == 0 else ""))
    print(f"  with a Date Read        : {sum(1 for r in rows if r['date_read'])}")
    print(f"  with a review           : {sum(1 for r in rows if r['review'])}")
    added = collections.Counter(r["date_added"] for r in rows).most_common(2)
    print(f"  most common Date Added  : {added}")
    if added and added[0][1] > 0.8 * len(rows):
        print("      ^ bulk single-session entry, NOT organic rating history")
    print("\n  rating distribution vs a typical Goodreads shelf:")
    for s in (1, 2, 3, 4, 5):
        got = dist.get(s, 0) / n if n else 0
        print(f"    {s}*  export {got * 100:5.1f}%   typical {TYPICAL_GOODREADS[s] * 100:5.1f}%")
    top = (dist.get(4, 0) + dist.get(5, 0)) / n if n else 0
    print(f"    4-5* share: export {top * 100:.1f}%  vs typical ~70%")
    if top < 0.6:
        print("      ^ FULL-RANGE ranking, not a top-heavy organic shelf:")
        print("        this export CANNOT measure the star-inflation risk.")

    # ── Join ────────────────────────────────────────────────────────────────
    joined = [(lib[norm_title(r['title'])], r["star"]) for r in rows
              if r["star"] and norm_title(r["title"]) in lib]
    if len(joined) < 20:
        print(f"\n  only {len(joined)} joined books — INCONCLUSIVE")
        return 1
    wa = np.asarray([j[0][0] for j in joined])
    authors = [j[0][1] for j in joined]
    genres = [j[0][2] for j in joined]
    stars = np.asarray([j[1] for j in joined])

    print(f"\n  joined to library       : {len(joined)} books")
    r_book = _pearson(stars, wa)
    print(f"  BOOK-LEVEL star vs WA   : r = {r_book:+.3f}, "
          f"rho = {_spearman(stars, wa):+.3f}")
    if r_book > 0.93:
        print("      ^ SUSPICIOUSLY HIGH for independent human rating — consistent")
        print("        with stars transcribed FROM the app. If so, everything")
        print("        below is circular. Confirm with the owner.")

    # ── The gate proper ─────────────────────────────────────────────────────
    a = offset_agreement("PER-AUTHOR", authors, wa, stars, MIN_N_AUTHOR)
    g = offset_agreement("PER-GENRE", genres, wa, stars, MIN_N_GENRE)

    print(f"\n{'-' * 72}\nSPLIT-HALF — out-of-sample transfer (the operational claim)\n{'-' * 72}")
    rs, n_elig = split_half(authors, wa, stars)
    if rs is None:
        print(f"  only {n_elig} author(s) with >= 4 books — INCONCLUSIVE")
    else:
        lo, hi = np.percentile(rs, [2.5, 97.5])
        print(f"  r = {np.mean(rs):+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  "
              f"({n_elig} authors with n>=4)"
              + ("   <- CI STRADDLES 0" if lo <= 0 <= hi else ""))

    print(f"\n{'=' * 72}\nVERDICT\n{'=' * 72}")
    for name, res in (("author", a), ("genre", g)):
        if res is None:
            print(f"  {name:6s}: INCONCLUSIVE")
            continue
        usable = res["sd_true"] * max(0.0, res["recovered"]) ** 0.5
        verdict = ("PASS" if usable >= 0.10 and res["ci"][0] > 0.3 else
                   "WEAK" if res["ci"][0] > 0 else "FAIL")
        print(f"  {name:6s}: {verdict}  (r={res['r']:+.3f}, "
              f"recovered={res['recovered'] * 100:.0f}%, usable={usable:.3f} WA)")
    print("\n  These verdicts are conditional on the PROVENANCE block above.")
    print("  Circular stars => meaningless. Zero to-read rows => coverage unknown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
