"""
obscure_book_test.py
====================
BEHAVIORAL VALIDATION: does the prediction engine DEGRADE GRACEFULLY on
genuinely obscure books — books neither the reader nor the model knows well?

We can't test ACCURACY here (no ground truth — these are unread, obscure books).
We test BEHAVIOR: does the engine know when it doesn't know?

THREE CHECKS
------------
1. Does confidence track real obscurity? Each test book has a PRE-COMMITTED
   obscurity rank (set by independent research BEFORE running, below). The
   engine's confidence/grounding should be higher for the less-obscure books
   and lower for the truly obscure ones. Uniform high confidence = FAIL.
2. Do predictions stay DISTINCT and plausible (no collapse to identical scores,
   no absurd 2.0 / 9.9 values)?
3. Does the reliability/grounding signal fire (low grounding for these, since
   they're in thin genres / unknown authors)?

PRE-COMMITTED OBSCURITY RANKING (1 = least obscure, 7 = most). Locked before
the engine ran, so the result can't be rationalized after the fact:
   1 And What Can We Offer You Tonight - Premee Mohamed   (award author, reviewed)
   2 Mothtown - Caroline Hardaker                          (small press, reviewed)
   3 Lungfish - Meghan Gilliss                             (indie debut, some coverage)
   4 The Wickwire Watch - Dewey Conway                     (indie YA, blog attention)
   5 12 Miles Below - Mark Arrows                          (web-serial, niche following)
   6 The Calamitous Bob - Daniel Schinhofen-style webserial(forum-only footprint)
   7 Little Blue Encyclopedia - Hazel Jane Plante          (tiny press, near-invisible)

PASS: engine confidence roughly higher at top, lower at bottom; scores distinct.
FAIL: uniform confidence regardless of obscurity, or collapsed/absurd scores.

HOW TO RUN (Thonny): press Run, confirm the small spend. Needs apikey.txt.
"""

import predict_engine as pe
import research_predict as rp

# (title, author, genre-guess, pre-committed obscurity rank)
TEST_BOOKS = [
    ("And What Can We Offer You Tonight", "Premee Mohamed", "Literary Fantasy", 1),
    ("Mothtown", "Caroline Hardaker", "Speculative Literary Fiction", 2),
    ("Lungfish", "Meghan Gilliss", "Literary Fiction", 3),
    ("The Wickwire Watch", "Dewey Conway", "Literary Fantasy", 4),
    ("12 Miles Below", "Mark Arrows", "Science Fantasy", 5),
    ("The Calamitous Bob", "Karpyshyn", "Epic Fantasy", 6),
    ("Little Blue Encyclopedia", "Hazel Jane Plante", "Literary Fiction", 7),
]


def main():
    print("=" * 70)
    print("OBSCURE-BOOK BEHAVIORAL TEST")
    print("=" * 70)
    print("Checking whether the engine's confidence tracks pre-committed obscurity")
    print("(1=least obscure ... 7=most). Pass = confidence degrades with obscurity.\n")

    go = input(f"Predict {len(TEST_BOOKS)} obscure books (~{len(TEST_BOOKS)} API calls)? (y/n): ")
    if go.strip().lower() != "y":
        print("Skipped.")
        return

    data = pe.build(source="db")
    books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data
    cache = rp.load_cache()
    client = rp.get_client()
    corr_models = rp.build_corr_models(books_e, cache)

    results = []
    print("\nPredicting...\n")
    for title, author, genre, rank in TEST_BOOKS:
        try:
            scores, conf, blurb, keywords, det_genre, words, from_cache = rp.research_book(
                title, author, genre, client, cache,
                allowed_genres=list(gw_e.keys()))
            rp.save_cache(cache)
            eff_genre = genre or det_genre
            p = rp.correct_and_predict(
                title, author, eff_genre, scores, conf, resid_sd,
                books_e, gw_e, gcw_e, cache,
                blurb=blurb, keywords=keywords, corr_models=corr_models)
        except Exception as e:
            print(f"  {title[:40]}: ERROR {e}")
            continue
        conf = p["conf"]
        wa = p["wa"]
        n_genre = p["n_genre"]
        n_author = p["n_author"]
        results.append((rank, title, conf, wa, n_genre, n_author))
        print(f"  [{rank}] {title[:38]:<38} conf={conf:<8} WA={wa:.2f}  "
              f"(genre n={n_genre}, author n={n_author})")

    print("\n" + "=" * 70)
    print("READOUT — sorted by pre-committed obscurity (1=least, 7=most)")
    print("=" * 70)
    results.sort()
    print(f"  {'Rank':<5}{'Book':<40}{'Confidence':<12}{'WA':<7}")
    for rank, title, conf, wa, ng, na in results:
        print(f"  {rank:<5}{title[:38]:<40}{str(conf):<12}{wa:<7.2f}")
    print()
    # Distinctness check
    was = [r[3] for r in results]
    distinct = len(set(round(w, 2) for w in was)) == len(was)
    span = max(was) - min(was)
    print(f"  Distinctness: {'PASS' if distinct else 'FAIL'} "
          f"({len(set(round(w,2) for w in was))} distinct of {len(was)}, "
          f"WA span {span:.2f})")
    plausible = all(2.0 <= w <= 9.8 for w in was)
    print(f"  Plausibility: {'PASS' if plausible else 'FAIL'} "
          f"(all scores in sane 2.0-9.8 range)")
    print()
    print("  CONFIDENCE-vs-OBSCURITY (the main test): read the table above.")
    print("  PASS if confidence/grounding is generally higher for ranks 1-3 and")
    print("  lower for ranks 5-7. FAIL if uniform regardless of obscurity.")
    print("  (Small n: judge the trend, not each individual book.)")


if __name__ == "__main__":
    main()
