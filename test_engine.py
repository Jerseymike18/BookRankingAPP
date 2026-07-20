"""
test_engine.py — the safety net
================================
Re-runnable correctness checks for the whole engine. Run this after ANY change
to the code or data, and it tells you, in seconds, whether you broke something.

This captures the verification logic that was previously done by hand and thrown
away. Now it's permanent: a single command that re-checks the things that must
always be true.

WHAT IT CHECKS
--------------
  1. Data loads (Excel and DB both open, both have books and components).
  2. WA reproduction: the engine's computed WA matches the stored WA for every
     book, to the penny. (Catches a broken weighting / roll-up.)
  3. Excel/DB drift (INFORMATIONAL, not a pass/fail): the DB is the live source
     of truth and the Excel workbook is import-only, so the two are SUPPOSED to
     diverge as books are added to the DB. Printed for visibility only.
  4. Prediction sanity: a prediction runs, returns a number in 0-10, with a
     confidence interval that brackets it and a sensible rank. (Catches a
     broken prediction pipeline.)
  5. Schema integrity: every genre in the data has weights; no rated book is
     missing a required (non-worldbuilding) component. (Catches bad data.)

HOW TO READ THE OUTPUT
----------------------
Every check prints PASS or FAIL. If everything is PASS, the engine is healthy.
Any FAIL points at exactly what broke, so you can fix it before it spreads.
Exit code is 0 if all pass, 1 if any fail (useful later for automation).

HOW TO RUN (Thonny): press Run.  (Or: python3 test_engine.py)
Needs: predict_engine.py, db_loader.py, and either books.db or the spreadsheet.
"""

import sys
import os
import numpy as np

import predict_engine as pe

# Track results
_results = []


def check(name, condition, detail=""):
    """Record a pass/fail with an optional detail message."""
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition, detail))
    line = f"  [{status}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line)
    return condition


# ---------------------------------------------------------------------------
# 1. Data loads
# ---------------------------------------------------------------------------
def test_data_loads():
    print("\n1. DATA LOADS")
    excel_ok = db_ok = False
    try:
        xb, xgw, xgcw = pe.load_everything()
        excel_ok = len(xb) > 0 and len(pe.components_of(xb)) > 0
        check("Spreadsheet loads", excel_ok,
              f"{len(xb)} books, {len(pe.components_of(xb))} components")
    except Exception as e:
        check("Spreadsheet loads", False, f"error: {e}")

    if os.path.exists("books.db"):
        try:
            import db_loader
            db, dgw, dgcw = db_loader.load_from_db()
            db_ok = len(db) > 0 and len(pe.components_of(db)) > 0
            check("Database loads", db_ok,
                  f"{len(db)} books, {len(pe.components_of(db))} components")
        except Exception as e:
            check("Database loads", False, f"error: {e}")
    else:
        check("Database loads", True, "books.db not present (skipped)")
    return excel_ok, db_ok


# ---------------------------------------------------------------------------
# 2. WA reproduction (the core math must match the stored values)
# ---------------------------------------------------------------------------
def test_wa_reproduction(source="db"):
    print(f"\n2. WA REPRODUCTION ({source})")
    if source == "db":
        # The DB is the live source the app runs on — this is the primary check.
        if not os.path.exists("books.db"):
            check("WA matches stored", True, "no DB (skipped)")
            return
        import db_loader
        books, gw, gcw = db_loader.load_from_db()
    else:
        # Excel is import-only; this secondary check confirms the importer's WA
        # roll-up stays internally consistent with the workbook's stored WA.
        books, gw, gcw = pe.load_everything()

    # Recompute each book's WA from components and compare to stored WA.
    cats = ["Story", "Character", "Theme", "Aesthetics", "Worldbuilding"]
    mismatches = 0
    worst = 0.0
    for _, b in books.iterrows():
        g = b["Genre"]
        if g not in gw:
            continue
        w = gw[g]
        wa = (b["WStory"] * (w["Story"] or 0) +
              b["WCharacter"] * (w["Character"] or 0) +
              b["WTheme"] * (w["Theme"] or 0) +
              b["WAesthetics"] * (w["Aesthetics"] or 0) +
              b["WWorldbuilding"] * (w["Worldbuilding"] or 0))
        d = abs(wa - b["WA"])
        if d > 1e-6:
            mismatches += 1
            worst = max(worst, d)
    check(f"All WAs match stored ({source})", mismatches == 0,
          "all match" if mismatches == 0
          else f"{mismatches} mismatches, worst Δ={worst:.4f}")


# ---------------------------------------------------------------------------
# 3. Excel vs DB drift (INFORMATIONAL — not a pass/fail)
# ---------------------------------------------------------------------------
# The DB is the live source of truth; the Excel workbook is import-only. The two
# are SUPPOSED to diverge as books are added to the DB, so a mismatch here is
# expected and is NOT a failure. This prints the current drift for visibility
# only — it records no pass/fail result and never affects the exit code.
def report_source_drift():
    print("\n3. EXCEL vs DB DRIFT  (informational, not a pass/fail)")
    if not os.path.exists("books.db"):
        print("  (no books.db present — nothing to compare)")
        return
    import db_loader
    xb, xgw, xgcw = pe.load_everything()
    db, dgw, dgcw = db_loader.load_from_db()

    # Book-set difference (the DB is expected to have more / differ over time).
    only_db = sorted(set(db["Book"]) - set(xb["Book"]))
    only_excel = sorted(set(xb["Book"]) - set(db["Book"]))
    print(f"  Books: Excel {len(xb)}, DB {len(db)}  "
          f"({len(only_db)} only in DB, {len(only_excel)} only in Excel)")
    if only_db:
        print(f"    only in DB: {', '.join(only_db[:5])}"
              + (" …" if len(only_db) > 5 else ""))
    if only_excel:
        print(f"    only in Excel: {', '.join(only_excel[:5])}"
              + (" …" if len(only_excel) > 5 else ""))

    # WA drift on the books both sources share.
    x = xb.set_index("Book")["WA"]
    d = db.set_index("Book")["WA"]
    common = x.index.intersection(d.index)
    if len(common):
        diff = (x[common] - d[common]).abs()
        print(f"  Shared books: {len(common)}, "
              f"max WA Δ={diff.max():.4f}, mean WA Δ={diff.mean():.4f}")
    print("  (drift expected — DB is the source of truth, Excel is import-only)")


# ---------------------------------------------------------------------------
# 4. Prediction sanity
# ---------------------------------------------------------------------------
def test_prediction_sanity():
    print("\n4. PREDICTION SANITY")
    try:
        data = pe.build(source="db")  # the live source the app runs on
        p = pe.predict("The Wise Man's Fear", "Patrick Rothfuss", "Epic Fantasy", data)
        wa = p["wa_final"]
        lo, hi = p["ci"]
        check("Prediction in 0-10 range", 0 <= wa <= 10, f"WA={wa:.2f}")
        check("CI brackets the prediction", lo <= wa <= hi,
              f"[{lo:.2f}, {hi:.2f}]")
        check("Rank is sensible", 1 <= p["rank"] <= p["total"],
              f"rank ~{p['rank']} of {p['total']}")
    except Exception as e:
        check("Prediction runs", False, f"error: {e}")


# ---------------------------------------------------------------------------
# 5. Schema / data integrity
# ---------------------------------------------------------------------------
def test_schema_integrity():
    print("\n5. SCHEMA & DATA INTEGRITY")
    import db_loader
    books, gw, gcw = db_loader.load_from_db()
    comps = pe.components_of(books)

    # every genre present has weights
    book_genres = set(books["Genre"].unique())
    missing = book_genres - set(gw.keys())
    check("Every genre has weights", not missing,
          "all covered" if not missing else f"missing: {sorted(missing)}")

    # no rated book missing a required (non-worldbuilding) component
    wb_comps = set(books.attrs["category_components"].get("Worldbuilding", []))
    bad = []
    for _, b in books.iterrows():
        for c in comps:
            if c in wb_comps:
                continue
            if isinstance(b[c], float) and np.isnan(b[c]):
                bad.append((b["Book"], c))
                break
    check("No missing required scores", not bad,
          "all complete" if not bad
          else f"{len(bad)} incomplete, e.g. {bad[0]}")


# ---------------------------------------------------------------------------
# 6. Analog shrinkage (empirical-Bayes baseline)
# ---------------------------------------------------------------------------
# Reference copy of the PRE-shrinkage per-component estimator (the original
# author>=2 / genre>=2 / global hard fallback). Used only to prove that
# estimate_components(mode="hard") is byte-identical to the old behaviour.
def _original_hard_estimate(books, author, genre, upstream):
    all_components = pe.components_of(books)
    by_author = books[books["Author"] == author]
    by_genre = books[books["Genre"] == genre]
    if len(by_author) >= 2:
        src_name, src = "author", by_author
    elif len(by_genre) >= 2:
        src_name, src = "genre", by_genre
    else:
        src_name, src = "global", books
    est = {}
    for comp in all_components:
        vals = src[comp].dropna()
        est[comp] = float(vals.mean()) if len(vals) else float(books[comp].dropna().mean())
    for target, model in upstream.items():
        coef, drivers = model["coef"], model["drivers"]
        if all(d in est for d in drivers):
            pred = coef[0] + sum(coef[k + 1] * est[drivers[k]] for k in range(len(drivers)))
            est[target] = 0.5 * est[target] + 0.5 * float(pred)
    return est, src_name, len(src)


def test_analog_shrinkage():
    print("\n6. ANALOG SHRINKAGE (empirical-Bayes baseline)")
    import db_loader
    books, gw, gcw = db_loader.load_from_db()
    comps = pe.components_of(books)

    # --- _shrink boundary behaviour (unit) --------------------------------
    check("shrink n=0 collapses exactly to parent",
          pe._shrink(0, 5.0, 7.3, 0.5) == 7.3,
          f"got {pe._shrink(0, 5.0, 7.3, 0.5)}")
    conv = pe._shrink(1e6, 8.5, 2.0, 0.5)
    check("shrink n>>k converges to the raw mean", abs(conv - 8.5) < 1e-4,
          f"{conv:.6f} -> 8.5")

    # --- (a) missing tiers collapse to parent (no fallback branching) ------
    genre = books["Genre"].mode()[0]
    by_genre = books[books["Genre"] == genre]
    est_a, _, _ = pe.estimate_components(books, "__NO_AUTHOR__", genre,
                                         upstream={}, mode="shrunk")
    est_g, _, _ = pe.estimate_components(books, "__NO_AUTHOR__", "__NO_GENRE__",
                                         upstream={}, mode="shrunk")
    collapse_a = collapse_g = True
    for c in comps:
        gv = books[c].dropna()
        glob = float(gv.mean()) if len(gv) else np.nan
        grv = by_genre[c].dropna()
        genre_hat = pe._shrink(len(grv),
                               float(grv.mean()) if len(grv) else 0.0,
                               glob, pe.K_GENRE)
        if not (np.isnan(genre_hat) and np.isnan(est_a[c])):
            collapse_a &= abs(est_a[c] - genre_hat) <= 1e-12
        if not (np.isnan(glob) and np.isnan(est_g[c])):
            collapse_g &= abs(est_g[c] - glob) <= 1e-12
    check("unseen author collapses to the genre estimate", collapse_a,
          f"genre='{genre}', n_g={len(by_genre)}")
    check("unseen author+genre collapses to the global mean", collapse_g)

    # --- (b) a data-rich author barely moves from its raw mean -------------
    big_author = books["Author"].value_counts().idxmax()
    n_big = int(books["Author"].value_counts().max())
    ba = books[books["Author"] == big_author]
    est_b, _, _ = pe.estimate_components(books, big_author,
                                         ba["Genre"].mode()[0],
                                         upstream={}, mode="shrunk")
    w_big = n_big / (n_big + pe.K_AUTHOR)          # author weight n/(n+k)
    budget = (1 - w_big) * 10.0                     # max possible move (0-10 scale)
    max_dev = max(abs(est_b[c] - float(ba[c].dropna().mean()))
                  for c in comps if len(ba[c].dropna()))
    check(f"data-rich author ({big_author}, n={n_big}) stays near raw mean",
          w_big >= 0.9 and max_dev <= budget + 1e-9,
          f"w={w_big:.3f}, max dev {max_dev:.3f} <= budget {budget:.3f}")

    # --- (c) hard mode is byte-identical to the pre-shrinkage engine -------
    upstream = pe.fit_upstream(books)
    pairs = books[["Author", "Genre"]].drop_duplicates().values.tolist()
    identical, worst = True, 0.0
    for author, genre_ in pairs:
        e_new, s_new, n_new = pe.estimate_components(books, author, genre_,
                                                     upstream, mode="hard")
        e_ref, s_ref, n_ref = _original_hard_estimate(books, author, genre_,
                                                       upstream)
        if s_new != s_ref or n_new != n_ref:
            identical = False
        for c in comps:
            dv = abs(e_new[c] - e_ref[c])
            worst = max(worst, dv)
            if dv != 0.0:
                identical = False
    check("hard mode byte-identical to original behaviour", identical,
          f"{len(pairs)} author/genre pairs, worst Δ={worst:.2e}")


# ---------------------------------------------------------------------------
# 7. Conformal prediction intervals (ADDITIVE — no point-prediction change)
# ---------------------------------------------------------------------------
def test_conformal_intervals():
    print("\n7. CONFORMAL PREDICTION INTERVALS")
    import os as _os
    import intervals
    import validate_engine as ve

    # (a) Bucket assignment matches the LOO bucket definition on the boundary
    #     values, and the SAME function backs both the table and live serving
    #     (definition drift would silently miscover).
    cases = {0: "genre-only n=0", 1: "author-only n=1", 2: "cluster 2<=n<6",
             5: "cluster 2<=n<6", 6: "cluster n>=6", 9: "cluster n>=6"}
    check("bucket assignment correct at n=0/1/2/5/6/9",
          all(intervals.density_bucket(k) == v for k, v in cases.items()),
          "boundaries 1->author-only, 2->2<=n<6, 6->cluster n>=6")
    check("LOO harness + serving share ONE bucket definition (no drift)",
          ve.intervals is intervals and ve.BUCKET_ORDER is intervals.BUCKET_ORDER)

    # (b) Pooling triggers strictly below MIN_BUCKET_N (=20) for the thin buckets,
    #     and never for the large author buckets (they have no pooling partner).
    thin = "author-only n=1"
    check("pooling triggers exactly below 20 (thin buckets only)",
          intervals.should_pool(thin, 19) is True
          and intervals.should_pool(thin, 20) is False
          and intervals.should_pool(thin, 21) is False
          and intervals.should_pool("genre-only n=0", 19) is True
          and intervals.should_pool("genre-only n=0", 20) is False
          and intervals.should_pool("cluster n>=6", 5) is False)

    # (c) A missing residuals.json yields no table and no interval, so the serving
    #     path omits the interval fields entirely (never invents a width).
    check("missing residuals.json -> interval omitted (None)",
          intervals.load_residuals("does_not_exist_zzz.json") is None
          and intervals.interval_for(None, 5) is None)

    # (d) Coverage / half-width math on a synthetic set with a known 80th pct:
    #     |resid| = 0..9 -> 80th pct = 7.2; 8 of 10 within -> coverage 0.80. The
    #     signed case confirms magnitudes (not signs) drive both.
    hw = ve.half_width_from_residuals(list(range(10)), target=0.80)
    cov = ve.coverage(list(range(10)), hw)
    signed = ve.coverage([-3.0, 3.0], ve.half_width_from_residuals([-3.0, 3.0]))
    check("coverage math correct on synthetic residuals (hw=7.2, cov=0.80)",
          abs(hw - 7.2) < 1e-9 and abs(cov - 0.80) < 1e-9 and abs(signed - 1.0) < 1e-9,
          f"hw={hw:.3f}, coverage={cov:.3f}")

    # (e) If the real table is present, its overall in-sample coverage must sit in
    #     the acceptance band [72%, 88%] — the gate, encoded as a regression guard.
    tbl = intervals.load_residuals(_os.path.join("calibration", "residuals.json"))
    if tbl is None:
        check("residuals.json overall coverage in [72%,88%]", True,
              "no table present (skipped)")
    else:
        ov = tbl.get("coverage", {}).get("overall")
        check("residuals.json overall coverage in [72%,88%]",
              ov is not None and 0.72 <= ov <= 0.88, f"overall={ov}")


# ---------------------------------------------------------------------------
# 8. Auto re-predict on add (baseline_repredict) — ADDITIVE
# ---------------------------------------------------------------------------
# Proves the on-add re-prediction (repredict_on_add.on_book_added): a finished
# book re-predicts exactly the unread cohort whose baseline it moved (same author
# always; same genre only past the gate), overwrites via db_write, and logs a
# tagged delta per row. HERMETIC: runs against a throwaway COPY of books.db with
# a synthetic in-memory research cache and no web researcher, so it never touches
# the real DB, the real cache, or the network.
def test_repredict_on_add():
    print("\n8. AUTO RE-PREDICT ON ADD")
    import tempfile, shutil, sqlite3, io, contextlib
    import os as _os
    import db_write
    import db_loader
    import research_predict as rp
    import repredict_on_add as rpa

    if not _os.path.exists("books.db"):
        check("repredict-on-add", True, "no books.db (skipped)")
        return

    FC = db_write.FICTION_COMPONENTS
    GENRE = "Epic Fantasy"  # a real genre → weights already exist in the copy
    # Per-component offsets break within-book collinearity so fit_regression is
    # well-posed (each category average differs).
    OFF = {}
    for c in ["Plot", "Entertainment", "Action", "Ending"]:      OFF[c] = 0.0
    for c in ["Depth", "Emotional Impact", "Motivations"]:       OFF[c] = 0.5
    for c in ["Prose", "Narration"]:                             OFF[c] = -0.5
    for c in ["Insights", "Thought-Provokingness"]:              OFF[c] = 0.2
    for c in ["Depth2", "Integration", "Originality"]:           OFF[c] = -0.2

    def vec(base, **ov):
        d = {c: float(base) + OFF[c] for c in FC}
        d.update({k: float(v) for k, v in ov.items()})
        return {c: min(10.0, max(0.0, v)) for c, v in d.items()}

    tmpd = tempfile.mkdtemp(prefix="repredict_test_")
    tmpdb = _os.path.join(tmpd, "books.db")
    shutil.copy2("books.db", tmpdb)

    def temp_engine():
        """Mirror pe.build(source='db') but read the throwaway DB explicitly."""
        books, gw, gcw = db_loader.load_from_db(tmpdb)
        coeffs, r2, resid_sd = pe.fit_regression(books)
        ginfo = pe.genre_bias_and_trust(books, coeffs)
        upstream = pe.fit_upstream(books)
        return books, gw, gcw, coeffs, r2, resid_sd, ginfo, upstream

    orig_db, orig_backed = db_write.DB, db_write._backed_up_this_session
    cache = {}
    try:
        db_write.DB = tmpdb
        db_write._backed_up_this_session = True  # no backup churn on the temp DB
        db_write._ensure_delta_log()             # guarantee delta_log + tag column

        con = sqlite3.connect(tmpdb)
        con.execute("DELETE FROM books")
        con.execute("DELETE FROM recommendations")
        con.execute("DELETE FROM delta_log")
        con.commit()
        con.close()

        # --- populate a controlled library (all writes via db_write) ----------
        with contextlib.redirect_stdout(io.StringIO()):
            # 8 background authors + 5 by Estab — all zero-deviation (llm == you),
            # so the genre baseline is stable and a zero-dev add can't move it.
            for i in range(8):
                t = f"BG{i}"
                db_write.add_book(t, GENRE, f"BGAuthor{i}", vec(6 + i % 3), words=100000)
                cache[t] = {"scores": vec(6 + i % 3), "conf": "test"}
            for i in range(5):
                t = f"EstabBook{i}"
                db_write.add_book(t, GENRE, "Estab", vec(6 + i % 3), words=100000)
                cache[t] = {"scores": vec(6 + i % 3), "conf": "test"}
            # unread recommendations: 2 Estab, 2 Newbie, 1 background peer
            for t, a in [("EstabRec0", "Estab"), ("EstabRec1", "Estab"),
                         ("NewbieRec0", "Newbie"), ("NewbieRec1", "Newbie"),
                         ("BGRec0", "BGAuthor0")]:
                db_write.add_recommendation(t, GENRE, a, vec(7.0), words=100000)
                cache[t] = {"scores": vec(7.0), "conf": "test"}
            # The finished book: Newbie's first data point, with a distinctive
            # author bias (you_Plot 8, llm_Plot 6 → author deviation +2 on Plot).
            db_write.add_book("NewbieTrig", GENRE, "Newbie", vec(8.0), words=100000)
        cache["NewbieTrig"] = {"scores": vec(8.0, Plot=6.0), "conf": "test"}

        eng = temp_engine()
        books, gw, gcw, resid_sd = eng[0], eng[1], eng[2], eng[5]

        # --- (1) brand-new author: n=0→1 shift is non-zero (unit, at engine) --
        peer_raw = {c: cache["NewbieRec0"]["scores"][c] for c in rp.LIVE}
        books_pre = books[books["Book"] != "NewbieTrig"]           # Newbie unseen
        r0 = rp.correct_and_predict("NewbieRec0", "Newbie", GENRE, peer_raw, "c",
                                    resid_sd, books_pre, gw, gcw, cache, corr_models=None)
        r1 = rp.correct_and_predict("NewbieRec0", "Newbie", GENRE, peer_raw, "c",
                                    resid_sd, books, gw, gcw, cache, corr_models=None)
        check("brand-new author (n=0→1) moves the cohort prediction",
              r0["n_author"] == 0 and r1["n_author"] == 1 and abs(r1["wa"] - r0["wa"]) > 1e-6,
              f"n {r0['n_author']}→{r1['n_author']}, ΔWA={r1['wa']-r0['wa']:+.3f}")

        # --- (5) ordering: only a COMMITTED trigger makes the pool read n=1 ----
        n_with = rpa._author_pool_n(books, cache, "Newbie")
        n_without = rpa._author_pool_n(books_pre, cache, "Newbie")
        check("ordering: committed trigger → pool sees n=1 (pre-commit sees n=0)",
              n_with == 1 and n_without == 0, f"committed n={n_with}, pre-commit n={n_without}")

        # --- (1 e2e / 3 write / 4 delta) run the real on-add path, dry_run=False
        before = _os.path.exists(tmpdb)
        with contextlib.redirect_stdout(io.StringIO()):
            rep = rpa.on_book_added("NewbieTrig", "Newbie", GENRE, vec(8.0),
                                    get_engine=temp_engine, cache=cache, web=None,
                                    corr_models=None, research_trigger=False,
                                    dry_run=False, verbose=False)
        aff = {r["title"]: r for r in (rep or {}).get("affected", [])}
        check("on-add re-predicts the new author's unread cohort",
              rep is not None and rep["trigger"]["author_is_new"]
              and "NewbieRec0" in aff and "NewbieRec1" in aff
              and all(aff[t]["reason"] == "author" for t in ("NewbieRec0", "NewbieRec1"))
              and abs(aff["NewbieRec0"]["d_wa"]) > 1e-6,
              f"author_is_new={rep['trigger']['author_is_new']}, "
              f"ΔWA(NewbieRec0)={aff.get('NewbieRec0', {}).get('d_wa')}")

        # (3) overwrite went through db_write.update_recommendation_scores: the
        # component scores changed in place while author/genre are preserved, and
        # the derived rank re-computes to a sensible value on the next read.
        con = sqlite3.connect(tmpdb)
        row = con.execute(
            'SELECT author, genre, "Plot" FROM recommendations WHERE title=?',
            ("NewbieRec0",)).fetchone()
        con.close()
        new_rank = aff.get("NewbieRec0", {}).get("new_rank")
        check("overwrite via db_write: scores changed in place, meta preserved, rank re-derives",
              row is not None and row[0] == "Newbie" and row[1] == GENRE
              and abs(row[2] - vec(7.0)["Plot"]) > 1e-6
              and isinstance(new_rank, int) and 1 <= new_rank <= len(books) + 1,
              f"stored Plot={row[2] if row else None:.2f} (was {vec(7.0)['Plot']:.2f}), new_rank={new_rank}")

        # (4) a baseline_repredict-tagged delta row per overwritten cohort book
        con = sqlite3.connect(tmpdb)
        tagged = con.execute(
            "SELECT title, tag FROM delta_log WHERE tag LIKE 'baseline_repredict:%'").fetchall()
        con.close()
        tagged_titles = {t for t, _ in tagged}
        check("delta_log: a 'baseline_repredict:<trigger>' row per overwritten book",
              {"NewbieRec0", "NewbieRec1"}.issubset(tagged_titles)
              and all(tag == "baseline_repredict:NewbieTrig" for _, tag in tagged),
              f"{len(tagged)} tagged rows: {sorted(tagged_titles)}")

        # --- (2) established author/genre: small affected set, gate suppresses --
        with contextlib.redirect_stdout(io.StringIO()):
            db_write.add_book("EstabTrig", GENRE, "Estab", vec(8.0), words=100000)
        cache["EstabTrig"] = {"scores": vec(8.0), "conf": "test"}   # zero deviation
        with contextlib.redirect_stdout(io.StringIO()):
            rep2 = rpa.on_book_added("EstabTrig", "Estab", GENRE, vec(8.0),
                                     get_engine=temp_engine, cache=cache, web=None,
                                     corr_models=None, research_trigger=False,
                                     dry_run=True, verbose=False)
        aff2 = {r["title"] for r in rep2["affected"]}
        supp2 = set(rep2["suppressed_genre_peers"])
        check("established add: affected set stays small (author-peers only, no sweep)",
              aff2 == {"EstabRec0", "EstabRec1"} and not rep2["genre_gate"]["fired"],
              f"affected={sorted(aff2)}, gate_fired={rep2['genre_gate']['fired']}")
        check("established add: genre gate suppresses same-genre peers below threshold",
              not rep2["genre_gate"]["fired"] and len(supp2) >= 3
              and "NewbieRec0" in supp2 and "NewbieRec1" in supp2,
              f"suppressed {len(supp2)} genre-peers, gate shift={rep2['genre_gate']['shift']} "
              f"<= {rep2['genre_gate']['gate']}")

        # --- (cap) genre-peer cohort is bounded; the overflow is REPORTED ------
        orig_cap, orig_gate = rpa.MAX_GENRE_PEERS_PER_ADD, rpa.GENRE_REPREDICT_GATE_CAP
        try:
            rpa.GENRE_REPREDICT_GATE_CAP = -1.0    # force the genre gate to fire
            rpa.MAX_GENRE_PEERS_PER_ADD = 1
            with contextlib.redirect_stdout(io.StringIO()):
                rep3 = rpa.on_book_added("NewbieTrig", "Newbie", GENRE, vec(8.0),
                                         get_engine=temp_engine, cache=cache, web=None,
                                         corr_models=None, research_trigger=False,
                                         dry_run=True, verbose=False)
            gkept = [r for r in rep3["affected"] if r["reason"] == "genre"]
            check("genre cohort cap bounds churn and reports the overflow (no silent cap)",
                  rep3["genre_gate"]["fired"] and len(gkept) == 1
                  and len(rep3["capped_genre_peers"]) >= 1,
                  f"gate fired, kept {len(gkept)} genre-peer(s), "
                  f"deferred {len(rep3['capped_genre_peers'])}")
        finally:
            rpa.MAX_GENRE_PEERS_PER_ADD, rpa.GENRE_REPREDICT_GATE_CAP = orig_cap, orig_gate
    finally:
        db_write.DB, db_write._backed_up_this_session = orig_db, orig_backed
        shutil.rmtree(tmpd, ignore_errors=True)


def test_delta_log_view():
    """Delta Log eligibility + dedup (delta_log_view.visible_rows).

    Guards the two brief requirements as a pure-logic regression:
      1. only genuinely-FINISHED books appear, and a `baseline_repredict:*`
         re-prediction audit row NEVER appears (even once its book is read);
      2. each book shows at most one row, preferring live > backfill > retro.
    Frozen-pred is structural: visible_rows passes pred_* through untouched."""
    import delta_log_view as dlv
    import db_write
    MARK = db_write.DELTA_BACKFILL_MARKER
    finished = {"lord of emperors", "tigana", "the name of the wind"}

    rows = [
        # unread same-author peer re-predicted on an add — must be hidden
        {"id": 10, "title": "Under Heaven", "logged_at": "2026-07-11T00:00:00Z",
         "tag": "baseline_repredict:Lord of Emperors", "pred_wa": 8.0, "act_wa": 8.2},
        # untagged row for an unread book (legacy anomaly) — must be hidden
        {"id": 11, "title": "A Song for Arbonne", "logged_at": "2026-07-04T00:00:00Z",
         "tag": None, "pred_wa": 7.0, "act_wa": 5.0},
        # genuine live pred-vs-actual for a finished book — must be shown
        {"id": 12, "title": "Lord of Emperors", "logged_at": "2026-07-15T00:00:00Z",
         "tag": None, "pred_wa": 8.1, "act_wa": 8.4},
        # finished book with a retro row AND a live row — dedup keeps the live one
        {"id": 13, "title": "The Name of the Wind", "logged_at": MARK,
         "tag": "retro_sweep_v1_shrunk", "pred_wa": 6.5, "act_wa": 6.9},
        {"id": 14, "title": "The Name of the Wind", "logged_at": "2026-07-16T00:00:00Z",
         "tag": None, "pred_wa": 6.6, "act_wa": 6.9},
        # finished book with only a workbook-backfill row — shown (best available)
        {"id": 15, "title": "Tigana", "logged_at": MARK,
         "tag": None, "pred_wa": 7.8, "act_wa": 7.5},
        # a STALE baseline_repredict row for a NOW-finished book — still hidden
        {"id": 16, "title": "Tigana", "logged_at": "2026-07-10T00:00:00Z",
         "tag": "baseline_repredict:Some Trigger", "pred_wa": 9.9, "act_wa": 1.1},
    ]

    shown = dlv.visible_rows(rows, finished, MARK)
    titles = [e["title"] for e in shown]
    by_id = {e["id"] for e in shown}

    check("delta-log view: unread books excluded (Under Heaven / A Song for Arbonne gone)",
          "Under Heaven" not in titles and "A Song for Arbonne" not in titles,
          f"shown={titles}")
    check("delta-log view: genuinely-read book shown (Lord of Emperors present)",
          "Lord of Emperors" in titles, f"shown={titles}")
    check("delta-log view: no baseline_repredict row survives (even when book is now read)",
          16 not in by_id and 10 not in by_id, f"ids shown={sorted(by_id)}")
    check("delta-log view: one row per book, live preferred over retro (Name of the Wind -> id 14)",
          titles.count("The Name of the Wind") == 1 and 14 in by_id and 13 not in by_id,
          f"ids shown={sorted(by_id)}")
    check("delta-log view: Tigana shows its genuine backfill row, not its stale repredict row",
          titles.count("Tigana") == 1 and 15 in by_id,
          f"ids shown={sorted(by_id)}")
    check("delta-log view: frozen pred passed through unchanged (no recompute)",
          next(e for e in shown if e["id"] == 12)["pred_wa"] == 8.1,
          "pred_wa for id 12 preserved")
    check("delta-log view: newest-first ordering preserved",
          [e["id"] for e in shown] == sorted((e["id"] for e in shown), reverse=True),
          f"ids={[e['id'] for e in shown]}")

    # read_order path: order by reading chronology, oldest-read first.
    read_order = {"lord of emperors": (2026, 7),
                  "the name of the wind": (2025, 4),
                  "tigana": (2026, 1)}
    chrono = dlv.visible_rows(rows, finished, MARK, read_order=read_order)
    check("delta-log view: read_order sorts oldest-read → newest-read",
          [e["title"] for e in chrono]
          == ["The Name of the Wind", "Tigana", "Lord of Emperors"],
          f"order={[e['title'] for e in chrono]}")
    # a finished book with no read date sorts last, not first.
    chrono2 = dlv.visible_rows(rows, finished, MARK,
                               read_order={"lord of emperors": (2026, 7),
                                           "the name of the wind": (2025, 4)})
    check("delta-log view: book with no read date sorts last chronologically",
          chrono2[-1]["title"] == "Tigana",
          f"order={[e['title'] for e in chrono2]}")


def main():
    print("=" * 60)
    print("ENGINE TEST SUITE")
    print("=" * 60)
    test_data_loads()
    test_wa_reproduction("db")      # primary: the live source the app runs on
    test_wa_reproduction("excel")  # secondary: importer internal consistency
    report_source_drift()          # informational only — records no pass/fail
    test_prediction_sanity()
    test_schema_integrity()
    test_analog_shrinkage()
    test_conformal_intervals()
    test_repredict_on_add()
    test_delta_log_view()

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print("\n" + "=" * 60)
    if passed == total:
        print(f"  ALL {total} CHECKS PASSED — the engine is healthy.")
    else:
        print(f"  {total - passed} of {total} CHECKS FAILED — see [FAIL] lines above.")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
