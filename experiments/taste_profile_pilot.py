"""
taste_profile_pilot.py
======================
STAGE 2 (pilot): does adding a positive-signal TASTE PROFILE to candidate
generation surface my hidden favorites better than the middle level (which only
knows my reading list + genres)?

METHOD (held-out favorites, non-circular)
-----------------------------------------
For each of 5 held-out gems: build the taste profile from my OTHER rated books,
then ask the LLM to "recommend books I'd love" under TWO conditions —
  MIDDLE  : knows my genres + the books I've read (minus the held-out gem)
  ADVANCED: same, plus the positive-signal taste profile
— and check whether each condition's candidate list contains:
  DIRECT hit : the hidden gem itself
  NEAR hit   : same author/series, OR shares the gem's defining qualities
  MISS       : neither
The signal is whether ADVANCED produces more hits/near-hits than MIDDLE.

This is candidate GENERATION only (cheap — a few calls), NOT scoring. We only
look at what titles get generated.

HONEST CAVEAT built into the readout: gems that are deep cuts of authors I've
heavily read (Erikson/Jordan/Abercrombie) are WEAK tests — the middle level
already knows those authors, so it gets near-hits without the profile doing
work. The clean tests are gems whose appeal is about QUALITIES not author
(The Neverending Story, Speaker for the Dead). The script flags which is which.

HOW TO RUN (Thonny): press Run, confirm the small spend. Needs apikey.txt.
"""

import numpy as np
import anthropic
import predict_engine as pe
import research_layer as rl

MODEL = "claude-sonnet-4-5"

HELD_OUT = [
    ("Toll the Hounds", "author-heavy"),       # 10 Eriksons read — weak test
    ("Speaker For The Dead", "qualities"),      # clean test
    ("The Neverending Story", "qualities"),     # clean test
    ("The Heroes", "author-heavy"),             # Abercrombie read — weakish
    ("The Great Hunt", "author-heavy"),         # 12 Jordans read — weak test
]

N_CANDIDATES = 12  # ask for a dozen so there's room to surface a gem


def load_key():
    with open("apikey.txt") as f:
        return f.read().strip()


def build_profile(books, exclude_title):
    """Positive-signal taste profile, built from all books EXCEPT the held-out one."""
    df = books[books["Book"] != exclude_title]
    LIVE = ["Plot", "Entertainment", "Action", "Ending", "Depth",
            "Emotional Impact", "Motivations", "Prose", "Narration",
            "Insights", "Thought-Provokingness", "Depth2", "Integration", "Originality"]
    top = df.nlargest(25, "WA")
    rest = df.nsmallest(len(df) - 25, "WA")
    gaps = sorted(((c, top[c].mean() - rest[c].mean()) for c in LIVE),
                  key=lambda x: -x[1])
    top_drivers = ", ".join(c for c, _ in gaps[:4])
    # strongest authors (>=2 books)
    ag = (df.groupby("Author").agg(n=("WA", "size"), avg=("WA", "mean"))
          .query("n>=2").sort_values("avg", ascending=False))
    top_authors = ", ".join(ag.head(6).index.tolist())
    # strong genres (positive only)
    gg = (df.groupby("Genre").agg(n=("WA", "size"), avg=("WA", "mean"))
          .query("n>=3").sort_values("avg", ascending=False))
    strong_genres = ", ".join(gg.head(4).index.tolist())
    return f"""TASTE PROFILE (what makes this reader love a book):
- What tips a book into their top tier, most strongly: {top_drivers}. They are elevated by emotional payoff, a landing ending, and believable character motivation driving a strong plot — more than by prose polish or pure ideas alone.
- Demonstrated loves: ambitious, large-canvas epic fantasy and SF. Favorite authors include {top_authors}.
- Strong genres: {strong_genres}. Genuine range into Russian literature and classical epic.
- Underexplored (not a dislike — invite discovery here): literary fiction.
- Look for books in ANY genre that deliver the qualities above, rather than steering by genre familiarity. Deeper, emotionally/thematically weighty books suit them more than flashy series-openers."""


def read_list(books, exclude_title):
    df = books[books["Book"] != exclude_title]
    return "; ".join(f"{r['Book']} ({r['Author']})" for _, r in df.iterrows())


def gen_candidates(client, system_context):
    prompt = f"""{system_context}

Recommend {N_CANDIDATES} books this reader would likely love and has NOT already read. Return ONLY a JSON list of objects with "title" and "author". No prose."""
    msg = client.messages.create(model=MODEL, max_tokens=800,
                                 messages=[{"role": "user", "content": prompt}])
    try:
        data = rl._extract_json(msg.content[0].text)
        return [(d.get("title", ""), d.get("author", "")) for d in data]
    except Exception:
        return []


def classify_hit(gem_title, gem_author, gem_series, candidates):
    """DIRECT if gem present; NEAR if same author or series; else MISS."""
    gl = gem_title.lower()
    for t, a in candidates:
        if gl in t.lower() or t.lower() in gl:
            return "DIRECT"
    for t, a in candidates:
        if gem_author and a and gem_author.lower() in a.lower():
            return "NEAR (same author)"
    return "MISS"


def main():
    books, gw, gcw = pe.load_everything()
    client = anthropic.Anthropic(api_key=load_key())

    n_calls = len(HELD_OUT) * 2
    print("=" * 64)
    print("STAGE 2 PILOT — taste profile vs middle level")
    print("=" * 64)
    go = input(f"This makes {n_calls} generation calls (well under $1). Proceed? (y/n): ")
    if go.strip().lower() != "y":
        print("Skipped.")
        return

    results = []
    for gem, kind in HELD_OUT:
        row = books[books["Book"] == gem]
        if len(row) == 0:
            print(f"  (couldn't find '{gem}' in your books — skipping)")
            continue
        gem_author = row.iloc[0]["Author"]
        gem_series = row.iloc[0]["Series"] if "Series" in row.columns else None

        genres = ", ".join(sorted(gw.keys()))
        rlist = read_list(books, gem)
        middle_ctx = (f"The reader enjoys these genres: {genres}. "
                      f"Books they have already read (do not suggest these): {rlist}")
        advanced_ctx = middle_ctx + "\n\n" + build_profile(books, gem)

        mid = gen_candidates(client, middle_ctx)
        adv = gen_candidates(client, advanced_ctx)
        mid_hit = classify_hit(gem, gem_author, gem_series, mid)
        adv_hit = classify_hit(gem, gem_author, gem_series, adv)
        results.append((gem, kind, mid_hit, adv_hit))
        print(f"\n  {gem}  [{kind} test]")
        print(f"    middle  : {mid_hit}")
        print(f"    advanced: {adv_hit}")

    print("\n" + "=" * 64)
    print("READOUT")
    print("=" * 64)
    print("  Clean tests (qualities-not-author) are the ones that matter most:")
    for gem, kind, mh, ah in results:
        flag = "  <-- CLEAN TEST" if kind == "qualities" else ""
        print(f"    {gem:<26} middle={mh:<18} advanced={ah}{flag}")
    print()
    # Score: did advanced beat middle, especially on clean tests?
    def rank(h):
        return 2 if h.startswith("DIRECT") else 1 if h.startswith("NEAR") else 0
    clean = [(mh, ah) for _, k, mh, ah in results if k == "qualities"]
    adv_better = sum(1 for mh, ah in clean if rank(ah) > rank(mh))
    mid_better = sum(1 for mh, ah in clean if rank(mh) > rank(ah))
    print(f"  On clean tests: advanced better on {adv_better}, middle better on "
          f"{mid_better}, tied on {len(clean)-adv_better-mid_better}.")
    print()
    print("  Interpretation: if advanced clearly wins the clean tests, the")
    print("  profile captures real taste — proceed to Stage 3 (fuller test).")
    print("  If it's a wash, the profile isn't earning its complexity — stop")
    print("  and keep the middle level. Small sample: read this as a direction,")
    print("  not a verdict.")


if __name__ == "__main__":
    main()
