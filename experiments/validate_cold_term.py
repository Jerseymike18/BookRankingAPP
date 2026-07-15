"""
Full-engine walk-forward validation of the word-count cold-start term
(experiments/cold_start_wordcount_spec.md). NOT the residual-stack proxy: this drives
the real engine functions end to end — rp.correct_and_predict for the honest baseline,
rp.fit_cold_start_term to fit the term on the past-only pool, and the real
apply_cold_start_term hook to apply it on the cold slice.

Compares two feature sets: word-count only vs word-count + series position. series_number
is read read-only from books.db and passed in as caller metadata (db_loader's frame does
not carry it and is a read-only core file).

Read-only, zero-API. Reproduces the committed 0.636 honest baseline as a sanity check.

Run:  python3 experiments/validate_cold_term.py
"""
import os, sys, argparse, sqlite3
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import predict_engine as pe
import db_loader
import db_backend
import research_predict as rp
import walkforward as wf

LIVE = rp.LIVE
BURN_IN = 15


def load_series_map():
    """{title: series_number} for the seed user, read-only from books.db."""
    uri = "file:" + os.path.join(ROOT, "books.db") + "?mode=ro"
    con = db_backend.connect(uri, uri=True)
    out = {str(t).strip(): sn for t, sn in con.execute(
        "SELECT title, series_number FROM books WHERE user_id=?",
        (db_backend.DEFAULT_USER_ID,))}
    con.close()
    return out


def run(min_pool, series_map=None):
    """series_map=None -> word-count-only term; a map -> word-count + series term."""
    books, gw, gcw = db_loader.load_from_db()
    cache = rp.load_cache()
    order, _ = wf.build_order(books, os.path.join(ROOT, "BookRankingsNew.xlsx"))
    title_row = {r["Book"]: r for _, r in books.iterrows()}
    words_of = dict(zip(books["Book"], books["Words"]))
    sn_of = series_map or {}

    recs = []
    for e in order:
        pos, title = e["position"], e["title"]
        if pos <= BURN_IN or title not in cache:
            continue
        scores = cache[title].get("scores")
        if not isinstance(scores, dict) or any(c not in scores for c in LIVE):
            continue
        raw = {c: float(scores[c]) for c in LIVE}
        conf = cache[title].get("conf", "?")
        author, genre = e["author"], e["genre"]

        pool_titles = [x["title"] for x in order if x["position"] < pos]
        books_pool = books[books["Book"].isin(pool_titles)]
        corr_models = rp.build_corr_models(books_pool, cache)
        resid_sd = pe.fit_regression(books_pool)[2]

        h = rp.correct_and_predict(title, author, genre, dict(raw), conf, resid_sd,
                                   books_pool, gw, gcw, cache, corr_models=corr_models)
        honest_wa, n_a, n_g = h["wa"], h["n_author"], h["n_genre"]

        if n_a == 0:
            pool_sn = {t: sn_of[t] for t in pool_titles if t in sn_of} if series_map else None
            coefs = rp.fit_cold_start_term(books_pool, cache, gw, gcw,
                                           corr_models=corr_models, min_pool=min_pool,
                                           series_map=pool_sn)
            t = rp.correct_and_predict(title, author, genre, dict(raw), conf, resid_sd,
                                       books_pool, gw, gcw, cache, corr_models=corr_models,
                                       words=words_of.get(title),
                                       series_number=sn_of.get(title), cold_term=coefs)
            term_wa = t["wa"]
        else:
            term_wa = honest_wa

        recs.append(dict(pos=pos, title=title, n_a=n_a, n_g=n_g,
                         actual=float(title_row[title]["WA"]),
                         honest=honest_wa, term=term_wa))
    return recs


def mae(rows, key):
    return float(np.mean([abs(r[key] - r["actual"]) for r in rows])) if rows else float("nan")


def report(recs, label):
    cold = [r for r in recs if r["n_a"] == 0]
    cold_gs = [r for r in cold if r["n_g"] > 0]
    noncold = [r for r in recs if r["n_a"] > 0]
    moved = sum(1 for r in cold if abs(r["term"] - r["honest"]) > 1e-9)
    print(f"\n=== {label} ===  (cold moved: {moved}/{len(cold)})")
    print(f"{'slice':16} {'honest':>8} {'+term':>8} {'Δ':>8}")
    for name, rows in (("OVERALL", recs), ("cold (n_a=0)", cold),
                       ("  genre-only", cold_gs), ("non-cold", noncold)):
        h, t = mae(rows, "honest"), mae(rows, "term")
        print(f"{name:16} {h:8.4f} {t:8.4f} {t-h:+8.4f}")
    return mae(recs, "term"), mae(cold, "term")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pool", type=int, default=25)
    args = ap.parse_args()

    smap = load_series_map()
    n_sn = sum(1 for v in smap.values() if v is not None)
    print(f"series_number populated for {n_sn}/{len(smap)} books "
          f"(standalone books -> in_series=0).")

    wc = run(args.min_pool, series_map=None)
    ws = run(args.min_pool, series_map=smap)
    o1, c1 = report(wc, "word-count ONLY")
    o2, c2 = report(ws, "word-count + series")

    print("\n" + "=" * 52)
    print(f"{'':22}{'overall':>10}{'cold':>10}")
    print(f"{'honest baseline':22}{mae(wc,'honest'):>10.4f}{mae([r for r in wc if r['n_a']==0],'honest'):>10.4f}")
    print(f"{'+ word-count':22}{o1:>10.4f}{c1:>10.4f}")
    print(f"{'+ word-count + series':22}{o2:>10.4f}{c2:>10.4f}")
    print(f"{'series adds (overall/cold)':22}{o2-o1:>+10.4f}{c2-c1:>+10.4f}")
    print("=" * 52)

    b, c, gw, gcw = load_books_cache()
    corr_full = rp.build_corr_models(b, c)
    lib = rp.fit_cold_start_term(b, c, gw, gcw, corr_models=corr_full, series_map=smap)
    if lib:
        names = ["log10(words)", "series_number", "in_series"]
        print("live-style full-library fit (word-count + series):")
        for nm, sl, mu in zip(names, lib["slopes"], lib["mu"]):
            print(f"   {nm:14} slope={sl:+.3f}  (mean {mu:.3f})")
        print(f"   intercept(bias)={lib['intercept']:+.3f}  n={lib['n']}")


def load_books_cache():
    b, gw, gcw = db_loader.load_from_db()
    return b, rp.load_cache(), gw, gcw


if __name__ == "__main__":
    main()
