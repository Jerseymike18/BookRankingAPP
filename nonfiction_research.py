"""
nonfiction_research.py
=====================
Grounded LLM scoring for NONFICTION, mirroring the fiction research layer's
approach (research_layer.LLMResearcher) but with the nonfiction rubric and the
12 nonfiction components. It REUSES the existing LLM plumbing rather than adding a
new path:

  * client  : research_predict.get_client()   (anthropic.Anthropic from apikey.txt)
  * model   : research_layer.MODEL             (claude-opus-4-8 — the calibrated
              grounded-research model per CLAUDE.md; nonfiction reuses it)
  * parsing : research_layer._extract_json     (the robust JSON extractor)
  * roll-up : nonfiction_engine.wa_from_components  (the SAME nonfiction math the
              rated books use, so a researched book is internally consistent)

The researcher returns the 12 nonfiction component scores + a confidence flag; the
roll-up turns them into category averages, a Total Average, and a (Quality-leaning)
WA. Nothing here writes to the DB — persisting a researched nonfiction book would
go through db_write.add_nonfiction_book, exactly like the rated ones.

COST: one short API call per book (a fraction of a cent). This module is wired
but makes no API call on import; call research_nonfiction_components() to spend.
"""

import research_layer as rl
import research_predict as rp
import nonfiction_engine as ne

# Reuse the fiction rubric's 0-10 scale + sentiment anchors verbatim (they are
# taste-general), with a nonfiction framing paragraph on top.
NONFICTION_RUBRIC = (
    "You are scoring a NONFICTION book. Judge it AS nonfiction — on the substance "
    "of what it conveys, the rigor of its reasoning, the quality of its writing, "
    "and the depth of its ideas, not as a story.\n\n"
    + rl.RUBRIC
)

# The 12 nonfiction components (2026 redesign — Brief 2), grouped by their five
# categories, each defined in the reader's framework. Several share NAMES with
# fiction (Entertainment/Prose/Insights/Thought-Provokingness) but mean what they
# mean for nonfiction here. Keys are the DB column names; the UI shows Entertainment
# as "Enjoyment" and Insights as "Insight". Order matches db_write.NONFICTION_COMPONENTS.
NONFICTION_COMPONENT_DEFS = {
    # SUBSTANCE — what it delivers, and how trustworthy
    "Informativeness": "How much substantive, accurate knowledge the book conveys.",
    "Accuracy": "Correctness, honesty, and sourcing — how much its claims can be trusted.",
    "Originality": "Novelty of its ideas and framing versus rehashing well-known material.",
    # REASONING — how it argues its case
    "Argumentation": "Logical rigor and persuasiveness of the case it makes.",
    "Evidence": "Quality, relevance, and integration of its evidence, data, and examples.",
    # EXPOSITION — how it is built and explained
    "Clarity": "How clearly it explains — accessibility of difficult material.",
    "Structure": "Organization and coherence of the whole; how well it builds.",
    # AESTHETICS — how it reads
    "Prose": "Sentence-level writing quality.",
    "Voice": "Distinctiveness and appeal of the authorial voice — personality, tone, wit.",
    # IMPACT — what it leaves the reader with
    "Insights": "Depth and quality of the understanding it produces.",
    "Thought-Provokingness": "How much it makes the reader think or reframes their view.",
    "Entertainment": "Sheer readability — how engaging and enjoyable it is to read.",
}
NONFICTION_COMPONENTS = list(NONFICTION_COMPONENT_DEFS)


def research_nonfiction_components(title, author, genre="Nonfiction",
                                   client=None, model=None):
    """Ask the LLM to score the 12 nonfiction components for one book, in the
    rubric. Returns (scores_dict, confidence). Reuses get_client / MODEL /
    _extract_json — no new LLM path. Makes one API call."""
    client = client or rp.get_client()
    model = model or rl.MODEL                      # Opus, the calibrated model
    comp_lines = "\n".join(
        f'  "{c}": {NONFICTION_COMPONENT_DEFS[c]}' for c in NONFICTION_COMPONENTS)
    prompt = f"""{NONFICTION_RUBRIC}

BOOK: "{title}" by {author}   (nonfiction{f'; {genre}' if genre and genre != 'Nonfiction' else ''})

Score ONLY these {len(NONFICTION_COMPONENTS)} components:
{comp_lines}

Respond with ONLY a JSON object mapping each component name to a number 0-10,
plus a "confidence" key ("high", "medium", or "low") for how well-known this
book is to you. No prose, no markdown, just the JSON. Example shape:
{{"Informativeness": 7.5, "Argumentation": 8.0, ..., "confidence": "medium"}}"""

    msg = client.messages.create(
        model=model, max_tokens=400,
        messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    data = rl._extract_json(text)
    confidence = data.pop("confidence", "unknown")
    scores = {c: float(data[c]) for c in NONFICTION_COMPONENTS if c in data}
    return scores, confidence


def researched_nonfiction_wa(scores, data, genre="Nonfiction"):
    """Roll researched components up to (wa, total_average, category_averages)
    via the SAME nonfiction math the rated books use. `data` is
    nonfiction_engine.load_nonfiction_from_db()'s (books, gw, gcw)."""
    books, gw, gcw = data
    wa, cat_avgs = ne.wa_from_components(scores, genre, gw, gcw)
    present = [v for v in cat_avgs.values() if v == v]   # drop NaN
    total = float(sum(present) / len(present)) if present else float("nan")
    return wa, total, cat_avgs


def research_and_predict(title, author, genre="Nonfiction", data=None):
    """Convenience: research the components, roll them up to WA/Total Average, and
    report the predicted rank by Total Average. Makes one API call."""
    data = data or ne.load_nonfiction_from_db()
    books = data[0]
    scores, conf = research_nonfiction_components(title, author, genre)
    wa, total, cat_avgs = researched_nonfiction_wa(scores, data, genre)
    bt = ne.add_total_average(books)
    rank = int((bt["Total Average"] > total).sum() + 1)
    return {"title": title, "author": author, "genre": genre,
            "scores": scores, "confidence": conf, "cat_avgs": cat_avgs,
            "wa": wa, "total_average": total, "rank": rank, "n": int(len(books)),
            "low_confidence": True}


def discover_nonfiction_candidates(request, n=8, client=None, model=None,
                                   avoid_titles=None):
    """Brainstorm real nonfiction book candidates for a free-text request. Uses
    the cheap DISCOVER_MODEL (Sonnet) — candidate generation gets no calibration
    benefit from Opus, mirroring the fiction Discover split. Returns a list of
    {"title", "author"} dicts (deduped, capped at n).

    ``avoid_titles``: iterable of titles already in the reader's library/TBR to
    drop (the read/saved backstop, done HERE — not by the caller — so a
    guaranteed single-book injection can bypass it; see below).

    Guaranteed single-book injection (parity with the fiction Discover flow): if
    the request NAMES one specific book, it is included as the FIRST candidate no
    matter what — even if the model wouldn't surface it or it is already in the
    library/TBR — deduped by lowercased title so a model duplicate collapses into
    it. Costs one extra cheap classifier call (shared with fiction)."""
    client = client or rp.get_client()
    model = model or rp.DISCOVER_MODEL
    avoid = {str(t).strip().lower() for t in (avoid_titles or ())}

    out, seen = [], set()
    # Explicit single-book request -> guarantee it, bypassing the avoid set.
    cls = rp._classify_request(request, client, model)
    if cls.get("is_single_book") and cls.get("title"):
        seen.add(cls["title"].lower())
        out.append({"title": cls["title"], "author": cls.get("author", "")})

    prompt = (
        f'Suggest {n} real, published NONFICTION books that match this request:\n'
        f'"{request}"\n\n'
        f'Return ONLY a JSON array of objects with "title" and "author" keys — '
        f'real nonfiction books (no fiction, no duplicates, no invented titles). '
        f'Example: [{{"title": "Sapiens", "author": "Yuval Noah Harari"}}]'
    )
    msg = client.messages.create(
        model=model, max_tokens=900,
        messages=[{"role": "user", "content": prompt}])
    data = rl._extract_json(msg.content[0].text.strip())
    for c in (data if isinstance(data, list) else []):
        if not isinstance(c, dict):
            continue
        t = str(c.get("title") or "").strip()
        a = str(c.get("author") or "").strip()
        k = t.lower()
        if t and k not in seen and k not in avoid:
            seen.add(k)
            out.append({"title": t, "author": a})
    return out[:n]


def report(r):
    print("=" * 64)
    print(f"NONFICTION RESEARCH  —  {r['title']}")
    print(f"            {r['author']}  |  {r['genre']}   [confidence: {r['confidence']}]")
    print("=" * 64)
    print("  scores:", {k: round(v, 1) for k, v in r["scores"].items()})
    print("  category averages:", {k: round(v, 2) for k, v in r["cat_avgs"].items()})
    print(f"  -> Total Average {r['total_average']:.2f}  |  WA {r['wa']:.2f}  "
          f"|  rank ~{r['rank']} of {r['n']} (by Total Average)")
    print(f"  ** grounded estimate; still low-confidence at n={r['n']} **")


if __name__ == "__main__":
    # Wired demo — makes ONE paid API call. Uncomment to run:
    # report(research_and_predict("Meditations", "Marcus Aurelius"))
    print("nonfiction_research wired. Components:", NONFICTION_COMPONENTS)
    print("Call research_and_predict(title, author) to research one book "
          "(one Opus API call).")
