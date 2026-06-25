"""
app.py — your book-rating system as a local website
===================================================
STEP 4: a browser interface over everything we've built. Wraps the engine
(predict_engine), the database reader (db_loader), and the safe write workflows
(db_write) into clickable pages — so your daily loop happens in a browser
instead of a spreadsheet.

PAGES
-----
  Rankings    : every rated book, sorted by Weighted Average, filterable.
  Add a Book  : the add_book form (validated — bad input is refused, not saved).
  Edit Ratings: pick a book, change component scores, see the WA update.
  Predict     : autonomous prediction for any book (WA, CI, rank).
  Read Queue  : view / reorder what to read next.

(LLM recommendations are intentionally held for a later pass, so this first
version has no API cost or network dependency.)

HOW TO RUN — this is NOT run like your other scripts:
  1. Install once (Thonny: Tools -> Manage Packages -> 'streamlit').
  2. In Terminal:  cd into your project folder, then:  streamlit run app.py
  3. It opens in your browser at http://localhost:8501  (local only).
  4. Stop it with Ctrl+C in the Terminal.

Needs: predict_engine.py, db_loader.py, db_write.py, books.db in this folder.
"""

import streamlit as st
import pandas as pd

import predict_engine as pe
import db_loader
import db_write
import research_predict as rp

st.set_page_config(page_title="Reading Ledger", page_icon="📖", layout="wide")

# ---------------------------------------------------------------------------
# Visual identity — a quiet "reading instrument", now after dark: deep ground,
# light ink, a single deep-red accent like a library stamp. Serif display,
# clean sans for data. (Dark base also set in .streamlit/config.toml.)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=Inter:wght@400;500;600&display=swap');

:root {
  --paper: #1b1714;        /* the dark ground (was the light page) */
  --ink: #ece6da;          /* light ink on dark (was dark ink on light) */
  --muted: #b3a995;        /* legible secondary text — not washed out */
  --rule: #3a342c;
  --stamp: #d4453d;        /* brighter red so it reads on the dark ground */
  --panel: #241f1a;        /* raised surfaces (sidebar, inputs) */
}
.stApp { background: var(--paper); }
html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: var(--ink); }

h1, h2, h3 { font-family: 'Fraunces', serif; color: var(--ink); letter-spacing: -0.01em; }
h1 { font-weight: 900; }

/* Widget labels + captions were washed out on dark — force legible colors. */
label, .stMarkdown, [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p {
  color: var(--ink) !important;
}
[data-testid="stCaptionContainer"], .stCaption, small { color: var(--muted) !important; }

.ledger-title {
  font-family: 'Fraunces', serif; font-weight: 900; font-size: 2.4rem;
  color: var(--ink);
  border-bottom: 3px double var(--ink); padding-bottom: 0.3rem; margin-bottom: 0.2rem;
}
.ledger-sub { color: var(--muted); font-size: 0.95rem; margin-bottom: 1.5rem; }

/* accent for buttons */
.stButton>button {
  background: var(--stamp); color: #1b1714; border: none; border-radius: 2px;
  font-weight: 600; letter-spacing: 0.02em;
}
.stButton>button:hover { background: #e85a51; color: #1b1714; }

[data-testid="stSidebar"] { background: var(--panel); border-right: 1px solid var(--rule); }

/* the WA "stamp" used on prediction results */
.wa-stamp {
  display:inline-block; border: 2px solid var(--stamp); color: var(--stamp);
  font-family:'Fraunces',serif; font-weight:900; font-size:2.2rem;
  padding:0.2rem 1rem; border-radius:4px; transform: rotate(-1.5deg);
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data loading (cached so it doesn't reload on every click; cleared on writes)
# ---------------------------------------------------------------------------
@st.cache_data
def load():
    books, gw, gcw = db_loader.load_from_db()
    return books, gw, gcw

def engine():
    # build the full predict tuple (not cached — it's cheap and always fresh)
    return pe.build(source="db")

@st.cache_resource
def get_corr_models():
    # Correlation-smoothing regressions (your_c ~ LLM of the other 13 components),
    # trained on your rated books. Stable, so built once per session and reused
    # across every research call rather than refit per prediction.
    books_db, _, _ = db_loader.load_from_db()
    return rp.build_corr_models(books_db, rp.load_cache())

COMPONENTS = db_write.FICTION_COMPONENTS

# ---------------------------------------------------------------------------
# Read Queue mood engine — reproduction of the old spreadsheet's mood blend.
# Each mood implicates a handful of components; dialing a mood up makes those
# components count more in the per-book weighted average ("mood score").
# ---------------------------------------------------------------------------
MOODS = {
    "Action-Heavy":    ["Action", "Plot", "Entertainment"],
    "Theme-Heavy":     ["Insights", "Thought-Provokingness"],
    "Emotion-Heavy":   ["Emotional Impact", "Depth"],
    "Immersion-Heavy": ["Depth2", "Originality", "Prose"],
    "Story-Heavy":     ["Plot", "Ending", "Entertainment"],
    "Character-Heavy": ["Depth", "Motivations"],
}


@st.cache_data
def load_recommendations(_gw, _gcw, _rated_wa):
    """Not-yet-done recommendations with their stored component scores, a WA
    computed exactly as db_loader does, and a predicted rank against the rated
    library. Cached — mood scoring happens live on top of this."""
    import sqlite3
    comp_cols = ",".join(f'"{c}"' for c in COMPONENTS)
    con = sqlite3.connect(db_write.DB)
    rows = con.execute(
        f"SELECT title,author,genre,series,words,blurb,keywords,{comp_cols} "
        f"FROM recommendations WHERE done=0").fetchall()
    con.close()

    recs = []
    for r in rows:
        title, author, genre, series, words, blurb, keywords = r[:7]
        comp_vals = dict(zip(COMPONENTS, r[7:]))
        rec = {"Book": (title or "").strip(),
               "Author": (author or "").strip(),
               "Genre": (genre or "Unknown").strip(),
               "Series": (series or "").strip().strip("'\""),
               "Words": words,
               "Blurb": blurb or "",
               "Keywords": keywords or ""}
        for c in COMPONENTS:
            v = comp_vals.get(c)
            rec[c] = float(v) if v is not None else float("nan")
        # WA via the same roll-up db_loader uses (category avgs * genre weights)
        wa = 0.0
        for cat in db_loader.CATEGORY_OF_INTEREST:
            wcat = db_loader._weighted_cat_avg(comp_vals, genre, cat, _gcw)
            wa += wcat * (_gw.get(genre, {}).get(cat, 0) or 0)
        rec["_wa"] = wa
        rec["Predicted Rank"] = int((_rated_wa > wa).sum() + 1)
        recs.append(rec)
    return pd.DataFrame(recs)


# ---------------------------------------------------------------------------
# Shared component-detail rendering — used by both Rankings and Recommendations
# so the 14-component breakdown is laid out identically (grouped by category).
# ---------------------------------------------------------------------------
def render_component_breakdown(row, category_components):
    """Lay out a single book's 14 component scores grouped by category
    (Story / Character / Aesthetics / Theme / Worldbuilding)."""
    for cat, comps in category_components.items():
        st.markdown(f"*{cat}*")
        cols = st.columns(len(comps))
        for col, comp in zip(cols, comps):
            v = row.get(comp)
            ok = v is not None and not (isinstance(v, float) and pd.isna(v))
            col.metric(comp, f"{float(v):.2f}" if ok else "—")


# ---------------------------------------------------------------------------
# Header + navigation
# ---------------------------------------------------------------------------
st.markdown('<div class="ledger-title">The Reading Ledger</div>', unsafe_allow_html=True)
st.markdown('<div class="ledger-sub">A working record of books read, rated, and predicted.</div>',
            unsafe_allow_html=True)

page = st.sidebar.radio("Go to", ["Rankings", "Add a Book", "Edit Ratings",
                                  "Predict", "Read Queue"])

books, gw, gcw = load()


# ---------------------------------------------------------------------------
# PAGE: Rankings
# ---------------------------------------------------------------------------
if page == "Rankings":
    st.subheader("Rankings")
    genres = ["All genres"] + sorted(books["Genre"].unique())
    pick = st.selectbox("Filter by genre", genres)
    show_comps = st.checkbox("Show component scores", value=False,
                             key="rank_show_comps",
                             help="Append the 14 component columns. Off by "
                                  "default so the table stays scannable.")
    view = books if pick == "All genres" else books[books["Genre"] == pick]
    view = view.sort_values("WA", ascending=False).reset_index(drop=True)
    view.index = view.index + 1
    show = view[["Book", "Author", "Genre", "WA"]].rename(
        columns={"Book": "Title", "WA": "Weighted Avg"})
    show["Weighted Avg"] = show["Weighted Avg"].round(2)
    if show_comps:
        for c in COMPONENTS:
            show[c] = view[c].round(2)
    st.caption(f"{len(view)} books"
               + ("" if pick == "All genres" else f" in {pick}"))
    st.dataframe(show, use_container_width=True, height=560)

    with st.expander("Inspect a book's component breakdown"):
        detail = st.selectbox("Book", view["Book"].tolist(),
                              key="rank_detail_book")
        brow = view[view["Book"] == detail].iloc[0]
        st.caption(f"{brow['Author']} · {brow['Genre']} · WA {brow['WA']:.2f}")
        render_component_breakdown(brow, books.attrs["category_components"])


# ---------------------------------------------------------------------------
# PAGE: Add a Book
# ---------------------------------------------------------------------------
elif page == "Add a Book":
    st.subheader("Add a Book")
    st.caption("Scores are 0–10. Worldbuilding (Depth2 / Integration / "
               "Originality) may be left at 0 for realist genres.")
    c1, c2 = st.columns(2)
    with c1:
        title = st.text_input("Title")
        author = st.text_input("Author")
        series = st.text_input("Series (optional)")
    with c2:
        genre = st.selectbox("Genre", sorted(gw.keys()))
        words = st.number_input("Word count", min_value=0, value=0, step=1000)
        year = st.number_input("Year read", min_value=1900, max_value=2100,
                               value=2026)

    st.markdown("**Component scores**")
    scores = {}
    cats = books.attrs["category_components"]
    for cat, comps in cats.items():
        st.markdown(f"*{cat}*")
        cols = st.columns(len(comps))
        for col, comp in zip(cols, comps):
            scores[comp] = col.number_input(comp, min_value=0.0, max_value=10.0,
                                             value=7.0, step=0.1, key=f"add_{comp}")

    if st.button("Add book to ledger"):
        valid = comps_present = True
        # use the real validated writer; capture its printed outcome
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            db_write.add_book(title, genre, author, scores,
                              series=series or None,
                              words=int(words) or None, year_read=int(year))
        out = buf.getvalue()
        if "✓" in out:
            st.success(out.strip().replace("✓", "").strip())
            st.cache_data.clear()
        else:
            st.error(out.strip().replace("✗", "").strip() or
                     "Could not add the book.")


# ---------------------------------------------------------------------------
# PAGE: Edit Ratings
# ---------------------------------------------------------------------------
elif page == "Edit Ratings":
    st.subheader("Edit Ratings")
    title = st.selectbox("Book", sorted(books["Book"].tolist()))
    row = books[books["Book"] == title].iloc[0]
    st.caption(f"{row['Author']} · {row['Genre']} · current WA "
               f"{row['WA']:.2f}")

    new = {}
    cats = books.attrs["category_components"]
    for cat, comps in cats.items():
        st.markdown(f"*{cat}*")
        cols = st.columns(len(comps))
        for col, comp in zip(cols, comps):
            cur = float(row[comp]) if pd.notna(row[comp]) else 0.0
            new[comp] = col.number_input(comp, min_value=0.0, max_value=10.0,
                                         value=cur, step=0.1, key=f"edit_{comp}")

    if st.button("Save changes"):
        # only send components that actually changed
        changed = {c: v for c, v in new.items()
                   if pd.isna(row[c]) or abs(v - float(row[c])) > 1e-9}
        if not changed:
            st.info("No changes to save.")
        else:
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                db_write.change_rating(title, changed)
            st.success(buf.getvalue().strip().replace("✓", "").strip())
            st.cache_data.clear()


# ---------------------------------------------------------------------------
# PAGE: Predict
# ---------------------------------------------------------------------------
elif page == "Predict":
    st.subheader("Predict a Book")
    st.caption("An autonomous estimate from your reading history. For books "
               "with few analogs the confidence interval widens accordingly.")
    c1, c2, c3 = st.columns(3)
    p_title = c1.text_input("Title", "")
    p_author = c2.text_input("Author", "")
    p_genre = c3.selectbox("Genre", sorted(gw.keys()))

    if st.button("Predict"):
        if not p_title or not p_author:
            st.error("Enter at least a title and author.")
        else:
            data = engine()
            p = pe.predict(p_title, p_author, p_genre, data)
            left, right = st.columns([1, 2])
            with left:
                st.markdown(f'<div class="wa-stamp">{p["wa_final"]:.2f}</div>',
                            unsafe_allow_html=True)
                st.caption("Predicted Weighted Average")
            with right:
                st.write(f"**90% interval:** {p['ci'][0]:.2f} – {p['ci'][1]:.2f}")
                st.write(f"**Predicted rank:** ~{p['rank']} of {p['total']} "
                         f"(range {p['rank_range'][0]}–{p['rank_range'][1]})")
                st.write(f"**Estimate basis:** {p['src']} (n={p['n_src']})")
                if p["n_genre"] < 5:
                    st.warning(f"Thin genre (n={p['n_genre']}): leaning on "
                               f"analogs — treat as rough.")
            st.markdown("**Estimated category averages**")
            cc = st.columns(4)
            for col, cat in zip(cc, ["Story", "Character", "Aesthetics", "Theme"]):
                col.metric(cat, f"{p['wcats'][cat]:.2f}")

    # =====================================================================
    # GROUNDED RESEARCH — the validated unfamiliar-book method: a RICHER prompt
    # scores THIS specific book in fine-grained decimals, then an AUTHOR+GENRE
    # hierarchical correction maps those scores onto your scale. The corrected
    # components are what get displayed and stored. (component MAE 0.837 vs the
    # old thin-prompt 1.05, validated leave-one-out against your real scores.)
    # =====================================================================
    st.divider()
    st.markdown("### Grounded research")
    st.caption("Scores *this* book specifically with a detailed rubric, then "
               "corrects the model's scores onto your scale using your rated "
               "books in the same genre and by the same author. Two different "
               "unfamiliar books in a genre get distinct, fine-grained scores.")

    # ----- 1. Single-book research --------------------------------------
    if st.button("Research this book"):
        if not p_title or not p_author:
            st.error("Enter a title and author above first.")
        else:
            data = engine()
            books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data
            cache = rp.load_cache()
            try:
                client = rp.get_client()
                with st.spinner("Researching this book… (one API call)"):
                    scores, conf, blurb, keywords, from_cache = rp.research_book(
                        p_title, p_author, p_genre, client, cache)
                rp.save_cache(cache)
                res = rp.correct_and_predict(
                    p_title, p_author, p_genre, scores, conf, resid_sd,
                    books_e, gw_e, gcw_e, cache, blurb=blurb, keywords=keywords,
                    corr_models=get_corr_models())
                st.session_state["single_research"] = (res, from_cache)
            except FileNotFoundError:
                st.error("apikey.txt not found — add your Anthropic key to research.")
                st.session_state.pop("single_research", None)
            except Exception as e:
                st.error(f"Research failed: {e}")
                st.session_state.pop("single_research", None)

    if "single_research" in st.session_state:
        res, from_cache = st.session_state["single_research"]
        st.markdown(f"**{res['title']}** — {res['author']} · {res['genre']}")
        left, right = st.columns([1, 2])
        with left:
            st.markdown(f'<div class="wa-stamp">{res["wa"]:.2f}</div>',
                        unsafe_allow_html=True)
            st.caption("Predicted WA (from corrected components)")
        with right:
            st.write(f"**90% interval:** {res['ci'][0]:.2f} – {res['ci'][1]:.2f}")
            st.write(f"**Predicted rank:** ~{res['rank']} of {res['total']}")
            if from_cache:
                st.caption("Reused cached research — no API call.")

        # Reliability signal: how well-grounded the author+genre correction was.
        n_g, n_a = res["n_genre"], res["n_author"]
        rel = st.success if n_g >= rp.WELL_SAMPLED_GENRE else st.warning
        rel(f"**Correction grounded in {n_g} rated {res['genre']} book(s) and "
            f"{n_a} by {res['author']}.**"
            + ("" if n_g >= rp.WELL_SAMPLED_GENRE else
               "  Thin genre — the correction leans on your global "
               "LLM-vs-you deviation, so treat this as lower-reliability."))
        # The model's own confidence on this specific book.
        conf = str(res["conf"]).lower()
        msg = f"**Model confidence on this book: {res['conf']}**"
        (st.warning if conf in ("low", "unknown", "?") else st.success)(msg)

        # Corrected component scores — fine-grained, author+genre corrected.
        # These are exactly what gets stored; the Read Queue's mood engine
        # ranks on them.
        st.markdown("**Corrected component scores** (author+genre corrected — "
                    "displayed, stored, and used by the mood engine)")
        rs = res["scores"]
        for cat, comps_in in books.attrs["category_components"].items():
            st.markdown(f"*{cat}*")
            cols = st.columns(len(comps_in))
            for col, comp in zip(cols, comps_in):
                v = rs.get(comp)
                col.metric(comp, f"{v:.2f}" if v is not None else "—")

        # Blurb + keywords from the SAME research call — stored alongside the
        # corrected components so they sit naturally with your existing entries.
        st.markdown("**Blurb**")
        st.write(res.get("blurb") or "_(none generated)_")
        st.markdown("**Keywords**")
        st.write(res.get("keywords") or "_(none generated)_")

        # Save this researched book to recommendations (CORRECTED components
        # stored; its WA/mood score then derive from those, like rated books).
        if st.button("Save to recommendations", key="save_single"):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                saved_ok = db_write.add_recommendation(
                    res["title"], res["genre"], res["author"], res["scores"],
                    blurb=res.get("blurb") or None,
                    keywords=res.get("keywords") or None)
            if saved_ok:
                st.success(f"Saved '{res['title']}' to recommendations. "
                           f"It will appear in the Read Queue mood results.")
                st.cache_data.clear()
            else:
                st.error(buf.getvalue().strip().replace("✗", "").strip()
                         or "Could not save the book.")

    # ----- 2. Series research -------------------------------------------
    st.divider()
    st.markdown("### Research a series")
    st.caption("Find the books in a series, confirm the spend, then research "
               "each one. Nothing is written until you choose to save.")
    series_name = st.text_input("Series name", key="series_name")

    if st.button("Find books in series"):
        if not series_name.strip():
            st.error("Enter a series name.")
        else:
            data = engine()
            gw_e = data[1]
            try:
                with st.spinner("Looking up the series… (one API call)"):
                    s_books, complete, note = rp.list_series(
                        series_name.strip(), list(gw_e.keys()))
                cache = rp.load_cache()
                for b in s_books:
                    b["cached"] = b.get("title") in cache
                st.session_state["series_list"] = {
                    "name": series_name.strip(), "books": s_books,
                    "complete": complete, "note": note}
                st.session_state.pop("series_results", None)
            except Exception as e:
                st.error(f"Series lookup failed: {e}")

    if "series_list" in st.session_state:
        sl = st.session_state["series_list"]
        s_books = sl["books"]
        if not s_books:
            st.warning("The model didn't return any books for that series.")
        else:
            n_cached = sum(1 for b in s_books if b.get("cached"))
            n_new = len(s_books) - n_cached
            st.markdown(f"**{sl['name']}** — {len(s_books)} books in reading order")
            preview = pd.DataFrame([{
                "#": i + 1, "Title": b.get("title"), "Author": b.get("author"),
                "Genre": b.get("genre"),
                "Status": "cached" if b.get("cached") else "new"}
                for i, b in enumerate(s_books)]).set_index("#")
            st.dataframe(preview, use_container_width=True)
            if not sl["complete"] or sl["note"]:
                st.warning("Model is not fully certain this list is complete"
                           + (f": {sl['note']}" if sl["note"] else "."))
            st.caption(f"{n_cached} already researched (free) · {n_new} new "
                       f"(~1¢ and a few seconds each).")

            if st.button(f"Confirm & research {len(s_books)} books"):
                data = engine()
                books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data
                cache = rp.load_cache()
                try:
                    client = rp.get_client()
                except Exception as e:
                    client = None
                    st.error(f"Cannot research: {e}")
                if client is not None:
                    results = []
                    prog = st.progress(0.0, text="Researching…")
                    for i, b in enumerate(s_books):
                        title = b.get("title"); author = b.get("author")
                        genre = b.get("genre")
                        try:
                            scores, conf, blurb, keywords, _ = rp.research_book(
                                title, author, genre, client, cache)
                            r = rp.correct_and_predict(
                                title, author, genre, scores, conf, resid_sd,
                                books_e, gw_e, gcw_e, cache,
                                blurb=blurb, keywords=keywords,
                                corr_models=get_corr_models())
                            r["series"] = sl["name"]
                            results.append(r)
                        except Exception as e:
                            results.append({"title": title, "author": author,
                                            "genre": genre, "error": str(e)})
                        prog.progress((i + 1) / len(s_books),
                                      text=f"Researched {i+1}/{len(s_books)}")
                    rp.save_cache(cache)
                    st.session_state["series_results"] = results

    if "series_results" in st.session_state:
        results = st.session_state["series_results"]
        ok = [r for r in results if "error" not in r]
        bad = [r for r in results if "error" in r]
        if ok:
            table = pd.DataFrame([{
                "Book": r["title"], "Author": r["author"], "Genre": r["genre"],
                "Predicted WA": round(r["wa"], 2),
                "Predicted Rank": r["rank"], "Confidence": r["conf"],
                "Genre n": r["n_genre"], "Author n": r["n_author"]}
                for r in ok])
            table.index = range(1, len(table) + 1)
            st.markdown("**Researched series**")
            st.dataframe(table, use_container_width=True)
            st.caption("‘Genre n’ / ‘Author n’ = how many of your rated books "
                       "grounded each correction; low numbers mean lower "
                       "correction reliability.")

            # The 14 CORRECTED component scores per book — these are what get
            # stored and what the Read Queue's mood engine ranks on.
            st.markdown("**Corrected component scores** (author+genre corrected "
                        "— stored and used by the mood engine)")
            comp_table = pd.DataFrame([
                dict(Book=r["title"],
                     **{c: r["scores"].get(c) for c in COMPONENTS})
                for r in ok]).set_index("Book")
            st.dataframe(comp_table.round(2), use_container_width=True)
        if bad:
            st.warning("Could not research: "
                       + ", ".join(f"{r['title']} ({r['error']})" for r in bad))

        if ok and st.button("Save to recommendations"):
            import io, contextlib
            buf = io.StringIO()
            saved = 0
            with contextlib.redirect_stdout(buf):
                for r in ok:
                    if db_write.add_recommendation(
                            r["title"], r["genre"], r["author"], r["scores"],
                            series=r.get("series"),
                            blurb=r.get("blurb") or None,
                            keywords=r.get("keywords") or None):
                        saved += 1
            st.success(f"Saved {saved} of {len(ok)} books to recommendations.")
            with st.expander("Details"):
                st.text(buf.getvalue().strip())
            st.cache_data.clear()


# ---------------------------------------------------------------------------
# PAGE: Read Queue
# ---------------------------------------------------------------------------
elif page == "Read Queue":
    st.subheader("Read Queue")
    tab_mood, tab_queue = st.tabs(["Mood Scores", "Queue"])

    # ----- TAB: Mood Scores (the mood engine over recommendations) ----------
    with tab_mood:
        st.caption("Dial in a mood, narrow with filters, and the not-yet-read "
                   "recommendations re-rank to match. All moods at 0 falls back "
                   "to predicted-rank order.")

        recs = load_recommendations(gw, gcw, books["WA"])
        if recs.empty:
            st.info("No outstanding recommendations to queue.")
        else:
            # --- (a) six combinable mood weights (0–5) ----------------------
            st.markdown("**Mood**")
            st.caption("How much each lens should pull the ranking (0 = ignore).")
            mood_weights = {}
            mcols = st.columns(3)
            for i, mood in enumerate(MOODS):
                mood_weights[mood] = mcols[i % 3].number_input(
                    mood, min_value=0.0, max_value=5.0, value=0.0, step=1.0,
                    key=f"mood_{mood}")

            # --- (b) attribute filters --------------------------------------
            st.markdown("**Filters**")
            f1, f2, f3 = st.columns(3)
            genres = ["All genres"] + sorted(g for g in recs["Genre"].unique() if g)
            f_genre = f1.selectbox("Genre", genres)
            f_length = f2.selectbox("Length",
                                    ["Any", "Short (<150K)", "Medium (150–300K)",
                                     "Long (>300K)"])
            f_type = f3.selectbox("Type", ["Any", "Series", "Standalone"])
            f4, f5 = st.columns(2)
            f_author = f4.text_input("Author contains", "")
            f_keyword = f5.text_input("Keyword contains", "")

            view = recs.copy()
            if f_genre != "All genres":
                view = view[view["Genre"] == f_genre]
            if f_length != "Any":
                w = view["Words"]
                if f_length.startswith("Short"):
                    view = view[w.notna() & (w < 150000)]
                elif f_length.startswith("Medium"):
                    view = view[w.notna() & (w >= 150000) & (w <= 300000)]
                else:
                    view = view[w.notna() & (w > 300000)]
            if f_type != "Any":
                has_series = view["Series"].str.len() > 0
                view = view[has_series if f_type == "Series" else ~has_series]
            if f_author.strip():
                view = view[view["Author"].str.contains(f_author.strip(),
                                                        case=False, na=False)]
            if f_keyword.strip():
                view = view[view["Keywords"].str.contains(f_keyword.strip(),
                                                          case=False, na=False)]

            # --- (c) mood score: components weighted by how much the dialed-up
            # moods implicate them, then a per-book weighted average of scores.
            impl = {c: 0.0 for c in COMPONENTS}
            for mood, comps in MOODS.items():
                w = mood_weights[mood]
                for c in comps:
                    impl[c] += w
            active = {c: wt for c, wt in impl.items() if wt > 0}

            def mood_score(r):
                num = den = 0.0
                for c, wt in active.items():
                    v = r[c]
                    if pd.notna(v):
                        num += float(v) * wt
                        den += wt
                return num / den if den > 0 else float("nan")

            if active and len(view):
                view = view.copy()
                view["Mood Score"] = view.apply(mood_score, axis=1)
                view = view.sort_values("Mood Score", ascending=False)
            else:
                view = view.copy()
                view["Mood Score"] = float("nan")
                view = view.sort_values("Predicted Rank", ascending=True)

            st.markdown("**Filtered Results**")
            st.caption(f"{len(view)} book(s)"
                       + ("" if not active else " — ranked by mood match"))
            rec_show_comps = st.checkbox(
                "Show component scores", value=False, key="rec_show_comps",
                help="Append the 14 component columns. Off by default so the "
                     "table stays scannable.")
            cols_show = ["Book", "Author", "Genre", "Words", "Mood Score",
                         "Predicted Rank", "Series", "Blurb", "Keywords"]
            if rec_show_comps:
                cols_show += [c for c in COMPONENTS]
            show = view[cols_show].copy()
            show["Mood Score"] = show["Mood Score"].round(2)
            if rec_show_comps:
                for c in COMPONENTS:
                    show[c] = show[c].round(2)
            show = show.reset_index(drop=True)
            show.index = show.index + 1
            st.dataframe(show, use_container_width=True, height=560)

            with st.expander("Inspect a book's component breakdown"):
                detail = st.selectbox("Book", view["Book"].tolist(),
                                      key="rec_detail_book")
                brow = view[view["Book"] == detail].iloc[0]
                st.caption(f"{brow['Author']} · {brow['Genre']} · "
                           f"predicted WA {brow['_wa']:.2f}")
                render_component_breakdown(
                    brow, books.attrs["category_components"])
                st.markdown("**Blurb**")
                st.write(brow["Blurb"] if str(brow["Blurb"]).strip()
                         else "_none yet_")
                st.markdown("**Keywords**")
                st.write(brow["Keywords"] if str(brow["Keywords"]).strip()
                         else "_none yet_")

                has_meta = (bool(str(brow["Blurb"]).strip())
                            and bool(str(brow["Keywords"]).strip()))
                if not has_meta:
                    st.caption("No blurb/keywords yet (added without research).")
                    if st.button("Generate blurb & keywords",
                                 key="gen_meta_btn"):
                        try:
                            client = rp.get_client()
                            with st.spinner("Generating… (one API call)"):
                                b_new, k_new = rp.generate_blurb_keywords(
                                    brow["Book"], brow["Author"], brow["Genre"],
                                    client)
                            if not b_new and not k_new:
                                st.warning("The model returned nothing usable "
                                           "for this (likely obscure) book — "
                                           "try again or add it manually.")
                            else:
                                db_write.set_recommendation_meta(
                                    brow["Book"], b_new or None, k_new or None)
                                st.success("Saved blurb & keywords.")
                                st.cache_data.clear()
                                st.rerun()
                        except FileNotFoundError:
                            st.error("apikey.txt not found — add your "
                                     "Anthropic key to generate.")
                        except Exception as e:
                            st.error(f"Could not generate: {e}")

    # ----- TAB: Queue (the original to-read order, editable) ----------------
    with tab_queue:
        import sqlite3
        con = sqlite3.connect(db_write.DB)
        q = [r[0] for r in con.execute(
            "SELECT title FROM read_queue ORDER BY position")]
        con.close()
        st.caption("Your to-read order. Edit the list and save to reorder.")
        text = st.text_area("One title per line (top = next up)",
                            "\n".join(q), height=300)
        if st.button("Save queue"):
            titles = [t.strip() for t in text.splitlines() if t.strip()]
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                db_write.update_queue(titles)
            st.success(buf.getvalue().strip().replace("✓", "").strip())
