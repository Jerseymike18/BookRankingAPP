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
    view = books if pick == "All genres" else books[books["Genre"] == pick]
    view = view.sort_values("WA", ascending=False).reset_index(drop=True)
    view.index = view.index + 1
    show = view[["Book", "Author", "Genre", "WA"]].rename(
        columns={"Book": "Title", "WA": "Weighted Avg"})
    show["Weighted Avg"] = show["Weighted Avg"].round(2)
    st.caption(f"{len(view)} books"
               + ("" if pick == "All genres" else f" in {pick}"))
    st.dataframe(show, use_container_width=True, height=560)


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
    # GROUNDED RESEARCH — fixes the "two unfamiliar books, same score" flaw
    # by scoring THIS specific book with the LLM, then auto-blending with the
    # analog estimate based on how much analog data exists for the author.
    # =====================================================================
    st.divider()
    st.markdown("### Grounded research")
    st.caption("The analog estimate above gives every unread-author book in a "
               "genre the same score. Research scores *this* book specifically, "
               "then blends — leaning on research when you have no analogs.")

    # ----- 1. Single-book research --------------------------------------
    if st.button("Research this book"):
        if not p_title or not p_author:
            st.error("Enter a title and author above first.")
        else:
            data = engine()
            books_e, gw_e, gcw_e, coeffs, r2, resid_sd, ginfo, upstream = data
            comps = pe.components_of(books_e)
            cache = rp.load_cache()
            try:
                researcher = rp.get_researcher(comps)
                with st.spinner("Researching this book… (one API call)"):
                    scores, conf, from_cache = rp.research_book(
                        p_title, p_author, p_genre, researcher, cache)
                rp.save_cache(cache)
                analog = pe.predict(p_title, p_author, p_genre, data)
                res = rp.blend(p_title, p_author, p_genre, scores, conf,
                               analog["wa_final"], resid_sd, books_e, gcw_e,
                               coeffs, ginfo, cache, comps)
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
            st.markdown(f'<div class="wa-stamp">{res["blend_wa"]:.2f}</div>',
                        unsafe_allow_html=True)
            st.caption("Blended WA (research + analog)")
        with right:
            st.write(f"**90% interval:** {res['ci'][0]:.2f} – {res['ci'][1]:.2f}")
            st.write(f"**Predicted rank:** ~{res['rank']} of {res['total']}")
            st.write(f"**Research WA:** {res['research_wa']:.2f}  ·  "
                     f"**Analog WA:** {res['analog_wa']:.2f}")
            if from_cache:
                st.caption("Reused cached research — no API call.")

        # Honesty signal (a): research vs analog lean
        wr = res["w_research"]
        st.info(f"**Leaned {wr*100:.0f}% on research, {(1-wr)*100:.0f}% on "
                f"analogs** — you've rated {res['n_author']} book(s) by this "
                f"author.")
        # Honesty signal (b): the model's own confidence in this book
        conf = str(res["conf"]).lower()
        msg = f"**Model confidence on this book: {res['conf']}**"
        (st.warning if conf in ("low", "unknown", "?") else st.success)(msg)
        # Taste-correction transparency
        if res["taste_applied"]:
            st.caption(f"Applied per-genre taste correction "
                       f"({res['taste_correction']:+.2f}) learned from "
                       f"{res['n_genre']} rated {res['genre']} books.")
        else:
            st.caption(f"Skipped taste correction — only {res['n_genre']} rated "
                       f"{res['genre']} book(s) (need ≥{rp.WELL_SAMPLED_GENRE}).")

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
                comps = pe.components_of(books_e)
                cache = rp.load_cache()
                try:
                    researcher = rp.get_researcher(comps)
                except Exception as e:
                    researcher = None
                    st.error(f"Cannot research: {e}")
                if researcher is not None:
                    results = []
                    prog = st.progress(0.0, text="Researching…")
                    for i, b in enumerate(s_books):
                        title = b.get("title"); author = b.get("author")
                        genre = b.get("genre")
                        try:
                            scores, conf, _ = rp.research_book(
                                title, author, genre, researcher, cache)
                            analog = pe.predict(title, author, genre, data)
                            r = rp.blend(title, author, genre, scores, conf,
                                         analog["wa_final"], resid_sd, books_e,
                                         gcw_e, coeffs, ginfo, cache, comps)
                            r["scores"] = scores
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
                "Blended WA": round(r["blend_wa"], 2),
                "Predicted Rank": r["rank"], "Confidence": r["conf"]}
                for r in ok])
            table.index = range(1, len(table) + 1)
            st.markdown("**Researched series**")
            st.dataframe(table, use_container_width=True)
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
                            series=r.get("series")):
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
            show = view[["Book", "Author", "Genre", "Words", "Mood Score",
                         "Predicted Rank", "Series", "Blurb", "Keywords"]].copy()
            show["Mood Score"] = show["Mood Score"].round(2)
            show = show.reset_index(drop=True)
            show.index = show.index + 1
            st.dataframe(show, use_container_width=True, height=560)

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
