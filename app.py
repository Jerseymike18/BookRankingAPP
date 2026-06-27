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

import datetime as _dt

import predict_engine as pe
import db_loader
import db_write
import research_predict as rp
import views

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
# Reliability helpers — grounding-first display of prediction trustworthiness.
# Data-grounding (genre-n / author-n) is the PRIMARY signal; LLM self-confidence
# is secondary and labeled as such.
# ---------------------------------------------------------------------------
def _grounding_label(n_genre, n_author):
    """Return (streamlit_fn, headline, detail) for a prediction's grounding."""
    if n_genre == 0:
        return (st.warning,
                "Very thin grounding — treat as a rough guess",
                f"No rated books in this genre ({n_author} by this author). "
                "The correction falls back to your global LLM-vs-you deviation, "
                "which is much less precise than genre- or author-anchored data.")
    elif n_genre <= 3 and n_author == 0:
        return (st.warning,
                "Thin grounding — lean on this less",
                f"Only {n_genre} rated book(s) in this genre, 0 by this author. "
                "Correction has little local data to work with.")
    elif n_genre >= 5 or n_author >= 1:
        extra = (f", {n_author} by this author" if n_author >= 1
                 else ", 0 by this author")
        return (st.success,
                "Strong grounding",
                f"Based on {n_genre} rated book(s) in this genre{extra}.")
    else:
        return (st.info,
                "Moderate grounding",
                f"Based on {n_genre} rated book(s) in this genre, "
                f"{n_author} by this author.")


def _show_grounding(n_genre, n_author, conf):
    """Render grounding (primary) + LLM conf (secondary) for one predicted book."""
    fn, headline, detail = _grounding_label(n_genre, n_author)
    fn(f"**{headline}** — {detail}")
    st.caption(f"Model self-confidence: {conf}  "
               "(the model's own assessment of how well it knows this book — "
               "less reliable than the data-grounding signal above)")


def _grounding_str(n_genre, n_author):
    """Short string label for table columns."""
    if n_genre == 0:
        return "very thin"
    elif n_genre <= 3 and n_author == 0:
        return "thin"
    elif n_genre >= 5 or n_author >= 1:
        return "strong"
    return "moderate"


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


@st.cache_data
def load_unread_pool():
    """Titles of UNREAD books — the TBR (recommendations not yet done) plus
    anything in the read queue — for the Reading Status pickers. Currently-reading
    and reading-next are by definition unread, so they come from here, NOT from
    the finished/rated books table. Read-only."""
    import sqlite3
    con = sqlite3.connect(db_write.DB)
    pool = {}
    for title, author, genre in con.execute(
            "SELECT title,author,genre FROM recommendations WHERE done=0"):
        t = (title or "").strip()
        if t:
            pool[t] = {"Author": (author or "").strip(),
                       "Genre": (genre or "").strip()}
    for (title,) in con.execute(
            "SELECT title FROM read_queue ORDER BY position"):
        t = (title or "").strip()
        if t and t not in pool:
            pool[t] = {"Author": "", "Genre": ""}
    con.close()
    return pool


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

# Navigation is grouped into sections so the growing page list stays legible:
#   Log      — the reading-log workflow (status, adding, editing).
#   Library  — every live-computed view of the rated library.
#   Stats    — display-only dashboards.
#   Discover — prediction/research and the read queue.
SECTIONS = {
    "Log":      ["Reading Status", "Add a Book"],
    "Library":  ["Rankings", "Tier List", "Year Views",
                 "Series Rankings", "Series Tier List"],
    "Stats":    ["Reading Stats", "Timeline"],
    "Discover": ["Predict", "Read Queue"],
}
section = st.sidebar.radio("Section", list(SECTIONS.keys()))
page = st.sidebar.radio("Go to", SECTIONS[section])

books, gw, gcw = load()
# Self-heal a stale cache: if the server was started before the reading-log
# columns (Status/Year) were added, the cached frame predates them. Clear the
# cache once and reload so the new views don't crash on a missing column.
if "Status" not in books.columns or "Year" not in books.columns:
    st.cache_data.clear()
    books, gw, gcw = load()

# Shared tier palette (the seven bands), reused by both tier-list views.
TIER_COLORS = {
    "S+": "#d4453d", "S": "#e08a3c", "A": "#d8c24a", "B": "#7fae6f",
    "C": "#5a9aa8", "D": "#8a7fae", "F": "#8a7d6b",
}


def render_tier_list(df_with_tier, label_col, caption_fmt):
    """Shared S+/S/A/B/C/D/F renderer for the book and series tier lists.
    `caption_fmt(row)` returns the small grey line shown under each title."""
    counts = views.tier_counts(df_with_tier)
    cap = "  ·  ".join(f"{t}: {counts[t]}" for t in views.TIER_ORDER)
    st.caption(cap)
    for tier in views.TIER_ORDER:
        band = df_with_tier[df_with_tier["Tier"] == tier]
        if band.empty:
            continue
        color = TIER_COLORS.get(tier, "#8a7d6b")
        st.markdown(
            f'<div style="margin-top:0.6rem;font-family:Fraunces,serif;'
            f'font-weight:900;font-size:1.3rem;color:{color}">{tier} '
            f'<span style="font-family:Inter;font-weight:500;font-size:0.85rem;'
            f'color:var(--muted)">({len(band)})</span></div>',
            unsafe_allow_html=True)
        for _, r in band.iterrows():
            st.markdown(
                f"- **{r[label_col]}** — <span style='color:var(--muted)'>"
                f"{caption_fmt(r)}</span>", unsafe_allow_html=True)


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

    st.caption(f"{len(view)} books"
               + ("" if pick == "All genres" else f" in {pick}"))

    # ── Edit / Delete controls ───────────────────────────────────────────────
    edit_title = st.selectbox("Select a book to edit or delete",
                              ["— select —"] + view["Book"].tolist(),
                              key="rank_edit_select")

    if edit_title != "— select —":
        row = view[view["Book"] == edit_title].iloc[0]
        st.caption(f"{row['Author']} · {row['Genre']} · WA {row['WA']:.2f}")

        cats = books.attrs["category_components"]
        new_scores = {}
        for cat, comps in cats.items():
            st.markdown(f"*{cat}*")
            cols = st.columns(len(comps))
            for col, comp in zip(cols, comps):
                cur = float(row[comp]) if pd.notna(row[comp]) else 0.0
                new_scores[comp] = col.number_input(
                    comp, min_value=0.0, max_value=10.0, value=cur, step=0.1,
                    key=f"rank_edit_{edit_title}_{comp}")

        save_col, del_col = st.columns([1, 1])
        if save_col.button("Save changes", key="rank_save"):
            changed = {c: v for c, v in new_scores.items()
                       if pd.isna(row[c]) or abs(v - float(row[c])) > 1e-9}
            if not changed:
                st.info("No changes to save.")
            else:
                import io, contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    db_write.change_rating(edit_title, changed)
                out = buf.getvalue().strip()
                if "✓" in out:
                    st.success(out.replace("✓", "").strip())
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(out.replace("✗", "").strip())

        # Two-step delete: first click arms it, second click fires.
        if "rank_delete_armed" not in st.session_state:
            st.session_state["rank_delete_armed"] = None
        if st.session_state["rank_delete_armed"] != edit_title:
            if del_col.button("Delete book", key="rank_del_arm"):
                st.session_state["rank_delete_armed"] = edit_title
                st.rerun()
        else:
            del_col.warning(f"Delete **{edit_title}** permanently?")
            dc1, dc2 = del_col.columns(2)
            if dc1.button("Yes, delete", key="rank_del_confirm"):
                import io, contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    db_write.delete_book(edit_title)
                out = buf.getvalue().strip()
                st.session_state["rank_delete_armed"] = None
                if "✓" in out:
                    st.success(out.replace("✓", "").strip())
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(out.replace("✗", "").strip())
            if dc2.button("Cancel", key="rank_del_cancel"):
                st.session_state["rank_delete_armed"] = None
                st.rerun()

    st.divider()

    # ── Rankings table (all component scores always shown) ───────────────────
    show = view[["Book", "Author", "Genre", "Series", "WA"]].rename(
        columns={"Book": "Title", "WA": "Weighted Avg"})
    show["Weighted Avg"] = show["Weighted Avg"].round(2)
    show["Series"] = show["Series"].fillna("").astype(str)
    for c in COMPONENTS:
        show[c] = view[c].round(2)
    st.dataframe(show, use_container_width=True, height=560)


# ---------------------------------------------------------------------------
# PAGE: Add a Book
# ---------------------------------------------------------------------------
elif page == "Add a Book":
    st.subheader("Add a Book")
    st.caption("Scores are 0–10. Worldbuilding (Depth2 / Integration / "
               "Originality) may be left at 0 for realist genres.")

    # ── Auto-fill section ────────────────────────────────────────────────────
    with st.expander("🔍 Look up book metadata (optional)", expanded=True):
        st.caption("Type a title and click Look up — the LLM will find author, "
                   "genre, word count, and series so you don't have to.")
        lk_col1, lk_col2 = st.columns([3, 1])
        lk_title = lk_col1.text_input("Title to look up",
                                       key="lookup_title",
                                       placeholder="e.g. The Name of the Wind")
        lk_author = lk_col2.text_input("Author hint (optional — helps disambiguate)",
                                        key="lookup_author_hint",
                                        placeholder="e.g. Rothfuss")

        if st.button("Look up"):
            if not lk_title.strip():
                st.error("Enter a title first.")
            else:
                try:
                    client = rp.get_client()
                    with st.spinner("Looking up book…"):
                        # Build a minimal author string so the prompt has
                        # something to anchor on; the LLM will correct it if wrong.
                        hint_author = lk_author.strip() or "unknown"
                        scores_raw, conf, blurb, keywords, det_genre, words_raw = \
                            rp.research_rich_plus(
                                client,
                                lk_title.strip(),
                                hint_author,
                                None,          # no genre — let the LLM detect it
                                allowed_genres=list(gw.keys()),
                            )
                    # Parse series from the blurb keywords heuristic: ask a
                    # second tiny structured call for the fields we need.
                    # Instead, repurpose the blurb to extract series ourselves
                    # with a lightweight follow-up prompt.
                    series_raw = ""
                    author_raw = hint_author if lk_author.strip() else ""
                    with st.spinner("Fetching author & series details…"):
                        meta_prompt = (
                            f'Return ONLY a JSON object with these keys:\n'
                            f'  "author": the correct full author name for "{lk_title.strip()}"\n'
                            f'  "series": the series name if the book belongs to one (empty string if standalone)\n'
                            f'  "series_number": the number within the series as an integer (0 if standalone or unknown)\n'
                            f'Respond with raw JSON only, no markdown.'
                        )
                        meta_msg = client.messages.create(
                            model=rp.rm.MODEL, max_tokens=200,
                            messages=[{"role": "user", "content": meta_prompt}])
                        meta_text = meta_msg.content[0].text.strip()
                        import re as _re
                        meta_text = _re.sub(r"^```(json)?|```$", "", meta_text,
                                            flags=_re.MULTILINE).strip()
                        import json as _json
                        meta = _json.loads(meta_text)
                        author_raw = meta.get("author", hint_author).strip() or hint_author
                        s_name = meta.get("series", "").strip()
                        s_num = meta.get("series_number", 0) or 0
                        if s_name and int(s_num) > 0:
                            series_raw = f"{s_name} #{int(s_num)}"
                        else:
                            series_raw = s_name

                    st.session_state["add_lookup_result"] = {
                        "title": lk_title.strip(),
                        "author": author_raw,
                        "genre": det_genre,
                        "words": words_raw,
                        "series": series_raw,
                        "blurb": blurb,
                    }
                except FileNotFoundError:
                    st.error("apikey.txt not found — add your Anthropic key.")
                except Exception as e:
                    st.error(f"Look-up failed: {e}")

        if "add_lookup_result" in st.session_state:
            r = st.session_state["add_lookup_result"]
            genre_str = r["genre"] or "(unknown)"
            words_str = f"~{r['words']:,}" if r["words"] else "(unknown)"
            series_str = r["series"] if r["series"] else "standalone"
            st.info(
                f"**Found:** {r['title']} by **{r['author']}** · "
                f"{genre_str} · {words_str} words · {series_str}"
            )
            if r["blurb"]:
                st.caption(r["blurb"])
            bc1, bc2 = st.columns(2)
            if bc1.button("✓ Use this — fill the form below"):
                st.session_state["add_prefill"] = dict(r)
                del st.session_state["add_lookup_result"]
                st.rerun()
            if bc2.button("✗ Wrong book — clear and try again"):
                del st.session_state["add_lookup_result"]
                st.rerun()

    # ── Pull any confirmed prefill ────────────────────────────────────────────
    _pf = st.session_state.get("add_prefill", {})
    _pf_genre_idx = (sorted(gw.keys()).index(_pf["genre"])
                     if _pf.get("genre") and _pf["genre"] in gw else 0)

    # ── Form fields (pre-filled if a lookup was confirmed, always editable) ───
    c1, c2 = st.columns(2)
    with c1:
        title = st.text_input("Title", value=_pf.get("title", ""), key="add_title")
        author = st.text_input("Author", value=_pf.get("author", ""), key="add_author")
        series = st.text_input("Series (optional)", value=_pf.get("series", ""),
                               key="add_series")
    with c2:
        genre = st.selectbox("Genre", sorted(gw.keys()), index=_pf_genre_idx,
                             key="add_genre")
        words = st.number_input("Word count", min_value=0,
                                value=int(_pf.get("words") or 0), step=1000,
                                key="add_words")
        year = st.number_input("Year read", min_value=1900, max_value=2100,
                               value=_dt.date.today().year, key="add_year")

    if _pf:
        st.caption("Metadata pre-filled from look-up — all fields are editable.")

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
            # Clear prefill so the form resets for the next book
            st.session_state.pop("add_prefill", None)
        else:
            st.error(out.strip().replace("✗", "").strip() or
                     "Could not add the book.")


# ---------------------------------------------------------------------------
# PAGE: Reading Status  (the BookTracker status block + reading-log controls)
# ---------------------------------------------------------------------------
elif page == "Reading Status":
    st.subheader("Reading Status")
    st.caption("What you're reading now and what's up next — chosen from your "
               "UNREAD books (TBR recommendations + read queue) — plus recently "
               "finished from your rated library.")

    def _rank_of(wa):
        return int((books["WA"] > wa).sum() + 1)

    # In-progress and upcoming are UNREAD by definition, so they're picked from
    # the unread pool (TBR + queue) — never from the finished/rated books table.
    pool = load_unread_pool()
    pool_titles = sorted(pool.keys())
    pool_set = set(pool_titles)
    # Drop any stale selections (e.g. a book since marked read) so the
    # multiselect options and stored value stay consistent.
    for _k in ("currently_reading", "reading_next"):
        if _k in st.session_state:
            st.session_state[_k] = [t for t in st.session_state[_k]
                                    if t in pool_set]
    reading = st.session_state.get("currently_reading", [])
    nxt = st.session_state.get("reading_next", [])

    def _meta(title):
        m = pool.get(title, {})
        return " · ".join(b for b in (m.get("Author"), m.get("Genre")) if b)

    def _unread_block(header, titles, empty_msg):
        st.markdown(f"**{header}**")
        if not titles:
            st.caption(empty_msg)
            return
        for t in titles:
            meta = _meta(t)
            tail = (f" — <span style='color:var(--muted)'>{meta}</span>"
                    if meta else "")
            st.markdown(f"- **{t}**{tail}", unsafe_allow_html=True)

    # Finished IS the rated library — books carry a year_read, so "recently
    # finished" = the most recent reading year.
    finished = books[books["Status"] == "finished"].dropna(subset=["Year"])
    if not finished.empty:
        last_year = int(finished["Year"].max())
        last_read = (finished[finished["Year"] == last_year]
                     .sort_values("WA", ascending=False))
    else:
        last_year, last_read = None, finished

    cols = st.columns(2)
    with cols[0]:
        _unread_block("📖 Currently reading", reading,
                      "Nothing marked currently-reading.")
        _unread_block("🔜 Reading next", nxt, "Nothing marked reading-next.")
    with cols[1]:
        st.markdown(f"**✓ Finished in {last_year}**" if last_year
                    else "**✓ Finished**")
        if last_read.empty:
            st.caption("No finished books with a year set.")
        else:
            for _, r in last_read.head(12).iterrows():
                yr = f" · {int(r['Year'])}" if pd.notna(r["Year"]) else ""
                st.markdown(
                    f"- **{r['Book']}** — <span style='color:var(--muted)'>"
                    f"{r['Author']} · {r['Genre']}{yr} · WA {r['WA']:.2f} · "
                    f"rank ~{_rank_of(r['WA'])} of {len(books)}</span>",
                    unsafe_allow_html=True)
            if len(last_read) > 12:
                st.caption(f"…and {len(last_read) - 12} more finished "
                           f"in {last_year}.")

    st.divider()
    st.markdown("### Update what you're reading")
    if not pool_titles:
        st.info("No unread books to pick from yet — research books on the "
                "Predict page or add titles to your read queue, and they'll be "
                "selectable here.")
    else:
        uc1, uc2 = st.columns(2)
        with uc1:
            st.multiselect(
                "📖 Currently reading", pool_titles, key="currently_reading",
                help="Pick from your unread TBR (researched recommendations) "
                     "and read queue.")
        with uc2:
            st.multiselect(
                "🔜 Reading next", pool_titles, key="reading_next",
                help="Pick from your unread TBR (researched recommendations) "
                     "and read queue.")

    st.divider()
    st.markdown("### Set / edit year read")
    y_book = st.selectbox("Book", sorted(books["Book"].tolist()),
                          key="year_book")
    cur_row = books[books["Book"] == y_book].iloc[0]
    cur_year = int(cur_row["Year"]) if pd.notna(cur_row["Year"]) \
        else _dt.date.today().year
    y_val = st.number_input("Year read", min_value=1900, max_value=2100,
                            value=cur_year, key="year_value")
    if st.button("Save year"):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = db_write.set_year_read(y_book, int(y_val))
        (st.success if ok else st.error)(
            buf.getvalue().strip().replace("✓", "").replace("✗", "").strip())
        if ok:
            st.cache_data.clear()
            st.rerun()


# ---------------------------------------------------------------------------
# PAGE: Tier List  (S+/S/A/B/C/D/F by Total Average)
# ---------------------------------------------------------------------------
elif page == "Tier List":
    st.subheader("Tier List")
    st.caption("Books banded by **Total Average** (the unweighted mean of the "
               "five category averages). S+ = Total Average ≥ 9.5; the rest fall "
               "into percentile bands (~9 / 15 / 25 / 25 / 15 / 10%).")
    bt = views.add_total_average(books)
    tl = views.tier_bands(bt, "Total Average", 9.5)
    render_tier_list(
        tl, "Book",
        lambda r: (f"{r['Author']} · {r['Genre']}"
                   + (f" · {r['Series']}" if r.get("Series") else "")
                   + f" · Total Avg {r['Total Average']:.2f} · WA {r['WA']:.2f}"))


# ---------------------------------------------------------------------------
# PAGE: Year Views  (books filtered by year_read, ranked)
# ---------------------------------------------------------------------------
elif page == "Year Views":
    st.subheader("Year Views")
    years = sorted((int(y) for y in books["Year"].dropna().unique()),
                   reverse=True)
    if not years:
        st.info("No books have a year_read set yet.")
    else:
        yc, gc = st.columns(2)
        year = yc.selectbox("Year read", years)
        yv = views.add_total_average(books[books["Year"] == year])
        genres = ["All genres"] + sorted(yv["Genre"].unique())
        pick = gc.selectbox("Filter by genre", genres)
        view = yv if pick == "All genres" else yv[yv["Genre"] == pick]
        view = view.sort_values("WA", ascending=False).reset_index(drop=True)
        view.index = view.index + 1
        st.caption(f"{len(view)} book(s) read in {year}"
                   + ("" if pick == "All genres" else f" · {pick}"))
        show = view[["Book", "Author", "Genre", "Series", "WA", "Total Average"]].rename(
            columns={"Book": "Title", "WA": "Weighted Avg"})
        show["Weighted Avg"] = show["Weighted Avg"].round(2)
        show["Total Average"] = show["Total Average"].round(2)
        show["Series"] = show["Series"].fillna("").astype(str)
        st.dataframe(show, use_container_width=True, height=560)


# ---------------------------------------------------------------------------
# PAGE: Series Rankings  (per-series rollup, ranked by average WA)
# ---------------------------------------------------------------------------
elif page == "Series Rankings":
    st.subheader("Series Rankings")
    st.caption("Ranked by **Adjusted WA** — avg WA plus a length bonus "
               "(0.0582 × (1.18^(n−1) − 1)) minus a short-series penalty "
               "(−0.2 per book below 3 read).")
    sa = views.series_aggregate(books)
    if sa.empty:
        st.info("No multi-book series found.")
    else:
        genres = ["All genres"] + sorted(sa["Genre"].unique())
        pick = st.selectbox("Filter by genre", genres)
        view = sa if pick == "All genres" else sa[sa["Genre"] == pick]
        st.caption(f"{len(view)} series")
        show = view[["Rank", "Series", "Author", "Genre", "Books",
                     "Avg WA", "Adjusted WA", "Avg Total Average"]].copy()
        show["Avg WA"] = show["Avg WA"].round(2)
        show["Adjusted WA"] = show["Adjusted WA"].round(3)
        show["Avg Total Average"] = show["Avg Total Average"].round(2)
        st.dataframe(show.set_index("Rank"), use_container_width=True, height=560)


# ---------------------------------------------------------------------------
# PAGE: Series Tier List  (series banded like the book tier list)
# ---------------------------------------------------------------------------
elif page == "Series Tier List":
    st.subheader("Series Tier List")
    st.caption("Series banded by **Adjusted WA** (length bonus − short-series "
               "penalty). S+ = ≥ 9.0; the rest fall into the same percentile "
               "bands as the book tier list.")
    sa = views.series_aggregate(books)
    if sa.empty:
        st.info("No multi-book series found.")
    else:
        sa = sa.rename(columns={"Adjusted WA": "Total Average"})
        stl = views.tier_bands(sa, "Total Average", 9.0)
        render_tier_list(
            stl, "Series",
            lambda r: f"{r['Author']} · {int(r['Books'])} books · "
                      f"Adj WA {r['Total Average']:.3f} · Avg WA {r['Avg WA']:.2f}")


# ---------------------------------------------------------------------------
# PAGE: Reading Stats  (BookTracker summary + genre/author rollups)
# ---------------------------------------------------------------------------
elif page == "Reading Stats":
    st.subheader("Reading Stats")
    rs = views.reading_stats(books)
    s = rs["summary"]
    m = st.columns(4)
    m[0].metric("Total books", s["total_books"])
    m[1].metric("Average WA", f"{s['avg_wa']:.2f}")
    m[2].metric("Average Total Avg", f"{s['avg_total_average']:.2f}")
    m[3].metric("Average word count",
                f"{s['avg_words']:,.0f}" if pd.notna(s["avg_words"]) else "—")

    st.markdown("**Per year**")
    py = rs["per_year"].copy()
    for c in ["Avg WA", "Avg Total Average"]:
        py[c] = py[c].round(2)
    py["Avg Words"] = py["Avg Words"].round(0)
    st.dataframe(py.set_index("Year"), use_container_width=True)

    st.markdown("**By genre**")
    bg = rs["by_genre"].copy()
    for c in ["Avg WA", "Avg Total Average"]:
        bg[c] = bg[c].round(2)
    bg["Avg Words"] = bg["Avg Words"].round(0)
    st.dataframe(bg.reset_index(drop=True), use_container_width=True, height=400)

    st.markdown("**By author**")
    ba = rs["by_author"].copy()
    ba["Avg WA"] = ba["Avg WA"].round(2)
    st.dataframe(ba.reset_index(drop=True), use_container_width=True, height=400)


# ---------------------------------------------------------------------------
# PAGE: Timeline  (reading/rating drift year to year)
# ---------------------------------------------------------------------------
elif page == "Timeline":
    st.subheader("Timeline")
    st.caption("How your reading and rating shift year to year — book counts, "
               "average WA, and the five category averages.")
    tl = views.timeline(books)
    if tl.empty:
        st.info("No books have a year_read set yet.")
    else:
        show = tl.copy()
        for c in ["Avg WA", "Story", "Character", "Aesthetics", "Theme",
                  "Worldbuilding"]:
            show[c] = show[c].round(2)
        show["Avg Words"] = show["Avg Words"].round(0)
        st.dataframe(show.set_index("Year"), use_container_width=True)

        st.markdown("**Books per year**")
        st.bar_chart(tl.set_index("Year")["Books"])
        st.markdown("**Category averages per year**")
        st.line_chart(tl.set_index("Year")[
            ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"]])


# ---------------------------------------------------------------------------
# PAGE: Predict
# ---------------------------------------------------------------------------
elif page == "Predict":
    st.subheader("Predict")
    st.caption("Two entry points into the same engine: name a book you have in "
               "mind, or ask the LLM to suggest books — either way YOUR validated "
               "pipeline does the scoring. Pick a mode below.")
    mode = st.radio(
        "Mode", ["Predict a book I name", "Discover books to predict"],
        horizontal=True, key="predict_mode",
        help="‘Predict a book I name’ scores a single title you enter (and can "
             "research a whole series). ‘Discover’ asks the LLM to propose "
             "candidates for a plain-language request, then scores each one "
             "through the same engine.")
    st.divider()

# --- MODE: Predict a book I name -------------------------------------------
if page == "Predict" and st.session_state.get("predict_mode") \
        == "Predict a book I name":
    GENRE_AUTO = "✨ Auto-detect (during research)"
    c1, c2, c3 = st.columns(3)
    p_title = c1.text_input("Title", "")
    p_author = c2.text_input("Author", "")
    p_genre_choice = c3.selectbox(
        "Genre", [GENRE_AUTO] + sorted(gw.keys()),
        help="Leave on Auto-detect to enter only title + author — grounded "
             "research will pick the genre from your list. Pick a genre manually "
             "to override, or to see the instant (non-LLM) estimate.")
    p_genre = None if p_genre_choice == GENRE_AUTO else p_genre_choice

    # --- Instant estimate: shown immediately, no button, no API call ---------
    st.markdown("### Instant estimate")
    st.caption("Free quick-look from your analogs — appears as soon as a title, "
               "author, and genre are set.")
    if p_title and p_author and p_genre is not None:
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
    elif p_title or p_author:
        st.info("Pick a genre above to see the instant estimate, or use "
                "‘Research this book’ below to auto-detect the genre.")
    else:
        st.caption("Enter a title and author to see the instant estimate.")

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
                    (scores, conf, blurb, keywords, det_genre, words,
                     from_cache) = rp.research_book(
                        p_title, p_author, p_genre, client, cache,
                        allowed_genres=list(gw_e.keys()))
                rp.save_cache(cache)
                # Genre: the user's pick if they made one, else the LLM's schema-
                # valid detection. Without either we can't correct onto your scale.
                eff_genre = p_genre or det_genre
                if eff_genre is None:
                    st.error("Couldn't auto-detect a genre from your list for "
                             "this book — pick a genre above and try again.")
                    st.session_state.pop("single_research", None)
                else:
                    res = rp.correct_and_predict(
                        p_title, p_author, eff_genre, scores, conf, resid_sd,
                        books_e, gw_e, gcw_e, cache, blurb=blurb,
                        keywords=keywords, corr_models=get_corr_models())
                    st.session_state["single_research"] = (
                        res, from_cache, words, p_genre is None)
            except FileNotFoundError:
                st.error("apikey.txt not found — add your Anthropic key to research.")
                st.session_state.pop("single_research", None)
            except Exception as e:
                st.error(f"Research failed: {e}")
                st.session_state.pop("single_research", None)

    if "single_research" in st.session_state:
        res, from_cache, words_est, was_auto = st.session_state["single_research"]
        genre_note = " · genre auto-detected" if was_auto else ""
        st.markdown(f"**{res['title']}** — {res['author']} · {res['genre']}"
                    f"{genre_note}")
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

        # Reliability signal: grounding (primary) then LLM conf (secondary).
        _show_grounding(res["n_genre"], res["n_author"], res["conf"])

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

        # Word count is an LLM ESTIMATE — pre-filled but editable, never treated
        # as authoritative. The (possibly corrected) value is what gets stored.
        st.markdown("**Word count** (LLM estimate — edit if you know better)")
        words_in = st.number_input(
            "Estimated word count", min_value=0,
            value=int(words_est) if words_est else 0, step=1000,
            key="single_words")

        # Save this researched book to recommendations (CORRECTED components
        # stored; its WA/mood score then derive from those, like rated books).
        if st.button("Save to recommendations", key="save_single"):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                saved_ok = db_write.add_recommendation(
                    res["title"], res["genre"], res["author"], res["scores"],
                    words=int(words_in) or None,
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
                            (scores, conf, blurb, keywords, det_genre, words,
                             _) = rp.research_book(
                                title, author, genre, client, cache,
                                allowed_genres=list(gw_e.keys()))
                            r = rp.correct_and_predict(
                                title, author, genre or det_genre, scores, conf,
                                resid_sd, books_e, gw_e, gcw_e, cache,
                                blurb=blurb, keywords=keywords,
                                corr_models=get_corr_models())
                            r["series"] = sl["name"]
                            r["words"] = words
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
                "Predicted Rank": r["rank"],
                "Grounding": _grounding_str(r["n_genre"], r["n_author"]),
                "Genre n": r["n_genre"], "Author n": r["n_author"],
                "Model self-conf": r["conf"]}
                for r in ok])
            table.index = range(1, len(table) + 1)
            st.markdown("**Researched series**")
            st.dataframe(table, use_container_width=True)
            st.caption(
                "**Grounding** is the primary reliability signal: how many of "
                "your rated books anchor the prediction (Genre n / Author n). "
                "Strong = many genre books or ≥1 by this author; thin/very thin "
                "= lean on the score less. "
                "**Model self-conf** is the LLM’s own assessment — less "
                "reliable than grounding, shown for reference only.")

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
                            series=r.get("series"), words=r.get("words"),
                            blurb=r.get("blurb") or None,
                            keywords=r.get("keywords") or None):
                        saved += 1
            st.success(f"Saved {saved} of {len(ok)} books to recommendations.")
            with st.expander("Details"):
                st.text(buf.getvalue().strip())
            st.cache_data.clear()


# --- MODE: Discover books to predict ---------------------------------------
# Same engine as the single-book flow, but the LLM GENERATES the candidates.
# Flow: free-text request -> LLM proposes candidates (no scores) -> you confirm
# the spend -> each candidate runs through research_book + correct_and_predict
# (the exact single-book pipeline, cache-reused) -> ranked by YOUR predicted WA
# -> save any into recommendations (so they flow into the TBR + mood queue).
if page == "Predict" and st.session_state.get("predict_mode") \
        == "Discover books to predict":
    st.markdown("### Discover books")
    st.caption("Ask for recommendations in plain language. The LLM proposes "
               "candidates aimed at your taste — and avoiding what you've already "
               "read — then YOUR engine scores and ranks each one. The model "
               "generates ideas; it never rates them.")

    d1, d2 = st.columns([4, 1])
    request = d1.text_input(
        "What are you in the mood for?",
        placeholder="recommend 5 epic fantasy books · a book like Toll the "
                    "Hounds but in a different genre · underrated sci-fi from "
                    "the 2010s",
        key="discover_request")
    max_cand = d2.number_input("Max", min_value=1, max_value=15, value=8,
                               step=1, key="discover_max",
                               help="Upper bound on how many candidates to "
                                    "generate (each one scored is one API call).")

    # ----- 1. Generate candidates (one idea-generation API call) ------------
    if st.button("Generate candidates"):
        if not request.strip():
            st.error("Type what you're looking for first.")
        else:
            try:
                client = rp.get_client()
                read_books = list(zip(books["Book"].tolist(),
                                      books["Author"].tolist()))
                # Also exclude books already on the TBR (recommendations table),
                # so Discover never re-suggests something already saved.
                import sqlite3
                con = sqlite3.connect(db_write.DB)
                tbr_books = [((t or "").strip(), (a or "").strip())
                             for t, a in con.execute(
                                 "SELECT title, author FROM recommendations")]
                con.close()
                with st.spinner("Asking for candidates… (one API call)"):
                    cands = rp.generate_candidates(
                        request.strip(), list(gw.keys()), read_books,
                        tbr_books=tbr_books, n=int(max_cand), client=client)
                cache = rp.load_cache()
                for c in cands:
                    c["cached"] = c.get("title") in cache
                st.session_state["discover_candidates"] = {
                    "request": request.strip(), "books": cands}
                st.session_state.pop("discover_results", None)
            except FileNotFoundError:
                st.error("apikey.txt not found — add your Anthropic key to discover.")
            except Exception as e:
                st.error(f"Candidate generation failed: {e}")

    # ----- 2. Confirmation step (controls cost before scoring) --------------
    if "discover_candidates" in st.session_state:
        dc = st.session_state["discover_candidates"]
        cands = dc["books"]
        if not cands:
            st.warning("The model didn't return any fresh candidates for that "
                       "request — try rephrasing, or widen it.")
        else:
            n_cached = sum(1 for c in cands if c.get("cached"))
            n_new = len(cands) - n_cached
            st.markdown(f"**Candidates for:** _{dc['request']}_")
            preview = pd.DataFrame([{
                "#": i + 1, "Title": c.get("title"), "Author": c.get("author"),
                "Genre": c.get("genre") or "✨ auto-detect",
                "Status": "cached" if c.get("cached") else "new"}
                for i, c in enumerate(cands)]).set_index("#")
            st.dataframe(preview, use_container_width=True)
            st.caption(f"{len(cands)} candidate(s) · none are already read or on "
                       f"your to-read list. Scoring them is **{len(cands)} API "
                       f"call(s)** "
                       f"({n_cached} already researched — free · {n_new} new, "
                       f"~1¢ and a few seconds each).")

            if st.button(f"Confirm & score {len(cands)} candidates"):
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
                    prog = st.progress(0.0, text="Scoring…")
                    for i, c in enumerate(cands):
                        title = c.get("title"); author = c.get("author")
                        genre = c.get("genre")
                        try:
                            (scores, conf, blurb, keywords, det_genre, words,
                             _) = rp.research_book(
                                title, author, genre, client, cache,
                                allowed_genres=list(gw_e.keys()))
                            eff_genre = genre or det_genre
                            if eff_genre is None:
                                raise ValueError(
                                    "couldn't auto-detect a genre from your list")
                            r = rp.correct_and_predict(
                                title, author, eff_genre, scores, conf,
                                resid_sd, books_e, gw_e, gcw_e, cache,
                                blurb=blurb, keywords=keywords,
                                corr_models=get_corr_models())
                            r["words"] = words
                            results.append(r)
                        except Exception as e:
                            results.append({"title": title, "author": author,
                                            "genre": genre, "error": str(e)})
                        prog.progress((i + 1) / len(cands),
                                      text=f"Scored {i+1}/{len(cands)}")
                    rp.save_cache(cache)
                    st.session_state["discover_results"] = results

    # ----- 3. Ranked results + save -----------------------------------------
    if "discover_results" in st.session_state:
        results = st.session_state["discover_results"]
        ok = [r for r in results if "error" not in r]
        bad = [r for r in results if "error" in r]
        # Ranked by YOUR predicted WA — the whole point: the engine, not the LLM,
        # orders them.
        ok.sort(key=lambda r: r["wa"], reverse=True)
        if ok:
            table = pd.DataFrame([{
                "Book": r["title"], "Author": r["author"], "Genre": r["genre"],
                "Predicted WA": round(r["wa"], 2),
                "Predicted Rank": r["rank"],
                "Grounding": _grounding_str(r["n_genre"], r["n_author"]),
                "Genre n": r["n_genre"], "Author n": r["n_author"],
                "Model self-conf": r["conf"]}
                for r in ok])
            table.index = range(1, len(table) + 1)
            st.markdown("**Discovered books — ranked by your predicted WA**")
            st.dataframe(table, use_container_width=True)
            st.caption(
                "**Grounding** is the primary reliability signal: how many of "
                "your rated books anchor the prediction (Genre n / Author n). "
                "Strong = many genre books or ≥1 by this author; thin/very thin "
                "= lean on the score less. "
                "**Model self-conf** is the LLM’s own assessment — less "
                "reliable than grounding, shown for reference only. "
                "Genre is auto-detected per book.")

            # The 14 corrected component scores per book (what gets stored and
            # what the mood engine ranks on) — same view as the series flow.
            st.markdown("**Corrected component scores** (author+genre corrected "
                        "— stored and used by the mood engine)")
            comp_table = pd.DataFrame([
                dict(Book=r["title"],
                     **{c: r["scores"].get(c) for c in COMPONENTS})
                for r in ok]).set_index("Book")
            st.dataframe(comp_table.round(2), use_container_width=True)

            # Save ANY of the scored candidates (selective) into recommendations,
            # with their corrected components, blurb, and keywords.
            st.markdown("**Save to your TBR**")
            to_save = st.multiselect(
                "Pick the discovered books to save to recommendations",
                [r["title"] for r in ok], key="discover_save_pick",
                help="Saved books flow into your TBR and the Read Queue mood "
                     "results, exactly like a single researched book.")
            if to_save and st.button("Save selected to recommendations"):
                import io, contextlib
                chosen = [r for r in ok if r["title"] in set(to_save)]
                buf = io.StringIO()
                saved = 0
                with contextlib.redirect_stdout(buf):
                    for r in chosen:
                        if db_write.add_recommendation(
                                r["title"], r["genre"], r["author"], r["scores"],
                                words=r.get("words"),
                                blurb=r.get("blurb") or None,
                                keywords=r.get("keywords") or None):
                            saved += 1
                st.success(f"Saved {saved} of {len(chosen)} discovered book(s) "
                           f"to recommendations. They'll appear in the Read "
                           f"Queue mood results.")
                with st.expander("Details"):
                    st.text(buf.getvalue().strip())
                st.cache_data.clear()
        if bad:
            st.warning("Could not score: "
                       + ", ".join(f"{r['title']} ({r['error']})" for r in bad))


# ---------------------------------------------------------------------------
# PAGE: Read Queue
# ---------------------------------------------------------------------------
if page == "Read Queue":
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
