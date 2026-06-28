"""
nonfiction_research.py
=====================
Grounded LLM scoring for NONFICTION, mirroring the fiction research layer's
approach (research_layer.LLMResearcher) but with the nonfiction rubric and the
8 nonfiction components. It REUSES the existing LLM plumbing rather than adding a
new path:

  * client  : research_predict.get_client()   (anthropic.Anthropic from apikey.txt)
  * model   : research_layer.MODEL             (claude-opus-4-8 — the calibrated
              grounded-research model per CLAUDE.md; nonfiction reuses it)
  * parsing : research_layer._extract_json     (the robust JSON extractor)
  * roll-up : nonfiction_engine.wa_from_components  (the SAME nonfiction math the
              rated books use, so a researched book is internally consistent)

The researcher returns the 8 nonfiction component scores + a confidence flag; the
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

# The 8 nonfiction components, grouped by category, with definitions in the
# reader's framework. (Entertainment/Prose/Insights/Thought-Provokingness share
# NAMES with fiction but mean what they mean for nonfiction here.)
NONFICTION_COMPONENT_DEFS = {
    # QUALITY
    "Informativeness": "How much substantive, accurate knowledge/information the book conveys.",
    "Argumentation": "Rigor, clarity, and persuasiveness of its reasoning and use of evidence.",
    "Entertainment": "Sheer readability — how engaging and enjoyable it is to read.",
    # AESTHETICS
    "Prose": "Sentence-level writing quality.",
    "Phraseology": "Craft and memorability of its phrasing / turns of phrase.",
    # THEME
    "Insights": "Quality and originality of its ideas and observations.",
    "Philosophizing": "Depth of its conceptual / philosophical engagement.",
    "Thought-Provokingness": "How much it makes the reader think.",
}
NONFICTION_COMPONENTS = list(NONFICTION_COMPONENT_DEFS)


def research_nonfiction_components(title, author, genre="Nonfiction",
                                   client=None, model=None):
    """Ask the LLM to score the 8 nonfiction components for one book, in the
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
