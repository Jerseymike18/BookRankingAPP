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

st.set_page_config(page_title="Reading Ledger", page_icon="📖", layout="wide")

# ---------------------------------------------------------------------------
# Visual identity — a quiet "reading instrument": warm paper, ink, a single
# deep-red accent like a library stamp. Serif display, clean sans for data.
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=Inter:wght@400;500;600&display=swap');

:root {
  --paper: #f6f3ec;
  --ink: #1f1b16;
  --muted: #6b6357;
  --rule: #d9d2c4;
  --stamp: #8c2f2a;
}
.stApp { background: var(--paper); }
html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: var(--ink); }

h1, h2, h3 { font-family: 'Fraunces', serif; color: var(--ink); letter-spacing: -0.01em; }
h1 { font-weight: 900; }

.ledger-title {
  font-family: 'Fraunces', serif; font-weight: 900; font-size: 2.4rem;
  border-bottom: 3px double var(--ink); padding-bottom: 0.3rem; margin-bottom: 0.2rem;
}
.ledger-sub { color: var(--muted); font-size: 0.95rem; margin-bottom: 1.5rem; }

/* accent for the active sidebar choice + buttons */
.stButton>button {
  background: var(--stamp); color: #f6f3ec; border: none; border-radius: 2px;
  font-weight: 600; letter-spacing: 0.02em;
}
.stButton>button:hover { background: #6f241f; color: #fff; }

[data-testid="stSidebar"] { background: #efeae0; border-right: 1px solid var(--rule); }

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
            scores[comp] = col.slider(comp, 0.0, 10.0, 7.0, 0.1, key=f"add_{comp}")

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
            new[comp] = col.slider(comp, 0.0, 10.0, cur, 0.1, key=f"edit_{comp}")

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


# ---------------------------------------------------------------------------
# PAGE: Read Queue
# ---------------------------------------------------------------------------
elif page == "Read Queue":
    st.subheader("Read Queue")
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
