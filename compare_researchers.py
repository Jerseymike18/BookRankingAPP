"""
compare_researchers.py
======================
PER-COMPONENT before/after MAE comparison of two researchers, run through the
EXISTING honesty test, to see component-by-component where web-grounding helps.

THE QUESTION
------------
The researcher estimates a book's 14 components; those flow through the validated
math to a predicted WA. We have two researchers:

  * MEMORY-BASED  — the richer-rubric prompt scored from the model's own
    knowledge (reresearch_and_measure.research_rich, Opus). This is the cached
    production grounding (llm_scores_richer.json).
  * WEB-GROUNDED  — the SAME rubric + SAME model, but it first uses web_search
    (restricted to goodreads.com, the existing WEB_SEARCH_TOOL) to retrieve what
    readers actually say about THIS book, and scores grounded in that evidence.

The ONLY variable between them is web grounding, so any per-component MAE
difference isolates what grounding adds (or costs).

THE HYPOTHESIS (tested, not assumed)
------------------------------------
Grounding should help components where this reader's taste tracks crowd
consensus (Plot, Entertainment, Action, Ending...) and may NOT help — or may
hurt — components where the reader diverges from consensus (Prose,
Thought-Provokingness, the literary dimensions). This app predicts THIS reader's
taste, not consensus quality, so the split tells us which components to trust the
crowd on and which to lean on the reader's own analog books for.

WHAT IT REUSES (no parallel measurement system, no math changes)
---------------------------------------------------------------
  * rl.stratified_sample(...)   — the same fixed sample evaluate_researcher uses.
  * rl.evaluate_researcher(...) — the honesty test, for the overall WA MAE of
    each researcher (predicted WA vs your actual WA, through the real math).
  * validate_engine per-component MAE logic — mean(|pred_c - actual_c|) with the
    same verdict bands; and ve.run_loo() per-component LOO MAE as the
    "already-well-predicted vs noisy" tag (step 3).

BLIND + FAIR
------------
Neither researcher ever sees your actual scores. Both are scored on the SAME
books; per-component and WA MAE are reported on the COMMON set both researchers
fully scored.

COST CONTROL
------------
Memory side reads the cache (free). Web side caches to web_grounded_cache.json
(resumable, never double-charges). Sample size is a parameter (--n-per-genre,
default 3 = the existing ~25-book stratified sample, here 32). Live web spend is
gated behind a printed estimate; pass --yes to authorize it.

RUN
---
  python compare_researchers.py                # prints cost estimate, then stops
  python compare_researchers.py --yes          # authorizes the live web run
  python compare_researchers.py --n-per-genre 5 --yes   # larger run (confirm!)
"""

import argparse
import datetime as _dt
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

import anthropic
import db_loader
import research_layer as rl
import reresearch_and_measure as rm
import research_predict as rp
import validate_engine as ve

# --- canonical references (no re-implementation) ----------------------------
LIVE = rm.LIVE                       # the 14 components, reference order
RESEARCH_MODEL = rm.MODEL            # claude-opus-4-8 — SAME on both sides
MEM_CACHE = rp.CACHE                 # llm_scores_richer.json (shared, read here)
WEB_CACHE = "web_grounded_cache.json"
WEB_SEARCH_TOOL = rp.WEB_SEARCH_TOOL  # goodreads-restricted server tool (reused)

EPS = 0.05                           # |delta| below this is "no change"

# The Worldbuilding category (CLAUDE.md scoring model). For realist genres these
# are scored 0 by convention (genre Worldbuilding weight == 0), so comparing a
# researcher's guess against a convention-0 measures nothing — we skip them there.
WB_COMPONENTS = {"Depth2", "Integration", "Originality"}

# Rough per-book cost of one Opus web_search-grounded call (input+output tokens
# plus ~2-3 server searches). Used only for the pre-spend estimate.
EST_COST_PER_WEB_BOOK = 0.12

# STEP 3 — cap on web_search server calls per book during grounded retrieval.
# Per-book review retrieval (this book's Goodreads rating + recurring praise /
# criticism) needs only a couple of searches; 3 bounds latency without starving
# it. Researcher-local (see WebGroundedResearcher.search_tool) so Discover's
# series enumeration keeps rp.WEB_SEARCH_TOOL's higher budget. Cached books are
# unaffected (the cache short-circuits before any search), so on a warm sample
# this is provably MAE-neutral; its effect on LIVE retrieval needs a live A/B.
SEARCH_MAX_USES = 3

# STEP 4 — staged model routing. The grounded path has two stages with different
# needs: RETRIEVAL (search Goodreads, summarise what readers report) is cheap
# summarisation; SCORING (evidence -> your 0-10 rubric) needs Opus judgement.
# StagedWebGroundedResearcher routes retrieval to a cheaper model and keeps
# scoring on RESEARCH_MODEL (Opus). Single-constant model swap (CLAUDE.md):
# set RETRIEVAL_MODEL to "claude-haiku-4-5-20251001" to test Haiku retrieval.
# ADOPT ONLY IF a live evaluate_researcher A/B shows WA MAE does not regress vs
# the Opus-agentic baseline — the Sonnet->Opus evidence handoff is lossy, so this
# is verified, not assumed.
RETRIEVAL_MODEL = "claude-sonnet-4-6"
STAGED_WEB_CACHE = "web_grounded_staged_cache.json"


# ---------------------------------------------------------------------------
# Researcher A: memory-based (richer rubric, Opus, NO web). Cache-first.
# ---------------------------------------------------------------------------
class MemoryResearcher:
    label = "memory"

    def __init__(self, key_path="apikey.txt"):
        self.cache = rp.load_cache(MEM_CACHE)
        self._client = None
        self._key_path = key_path
        self._lock = threading.Lock()

    def _client_lazy(self):
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=rl.load_key(self._key_path), max_retries=rp.LLM_MAX_RETRIES)
        return self._client

    def research(self, title, author, genre):
        e = rl.cache_lookup(self.cache, title)
        if e and "scores" in e:
            return ({c: float(e["scores"][c]) for c in LIVE if c in e["scores"]},
                    e.get("conf", "cache"))
        scores, conf = rm.research_rich(self._client_lazy(), title, author, genre)
        with self._lock:
            self.cache[title] = {"scores": scores, "conf": conf}
            rp.save_cache(self.cache, MEM_CACHE)
        return scores, conf


# ---------------------------------------------------------------------------
# Researcher B: web-grounded. SAME rubric (rm.rich_prompt) + a grounding step
# that pulls Goodreads reader evidence via the existing web_search tool.
# ---------------------------------------------------------------------------
_GROUNDING = """

GROUNDING REQUIREMENT: Before scoring, use the web_search tool (it is restricted
to goodreads.com) to find what readers actually report about THIS specific book —
its Goodreads rating, the recurring praise, and the recurring criticisms. Base
each component score on that retrieved reader evidence, not on the author's
general reputation or your prior impression of the book. After searching, respond
with ONLY the JSON object described above (the 14 components + "confidence"). No
prose, no markdown."""


class WebGroundedResearcher:
    label = "grounded"

    def __init__(self, key_path="apikey.txt", cache_path=WEB_CACHE,
                 model=RESEARCH_MODEL, search_max_uses=SEARCH_MAX_USES):
        self.cache_path = cache_path
        self.cache = rp.load_cache(cache_path)
        self.model = model
        self._client = None
        self._key_path = key_path
        self._lock = threading.Lock()
        # STEP 3 — cap search breadth. A researcher-LOCAL copy of the goodreads
        # tool with a small max_uses, so per-book review retrieval can't sprawl
        # into many searches. We do NOT mutate rp.WEB_SEARCH_TOOL (max_uses=5),
        # which Discover's series enumeration still needs to page a series list.
        self.search_tool = {**WEB_SEARCH_TOOL, "max_uses": search_max_uses}

    def _client_lazy(self):
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=rl.load_key(self._key_path), max_retries=rp.LLM_MAX_RETRIES)
        return self._client

    def research(self, title, author, genre):
        e = rl.cache_lookup(self.cache, title)
        if e and "scores" in e and all(c in e["scores"] for c in LIVE):
            return ({c: float(e["scores"][c]) for c in LIVE},
                    e.get("conf", "cache"))
        # Durable store before the ~38-110s web_search: a book grounded at runtime
        # survives redeploys (the JSON write is ephemeral on Railway). One cheap read
        # on a file miss only; best-effort so a DB hiccup never blocks grounding.
        e = rp.db_cache_get(self.cache_path, title)
        if e and "scores" in e and all(c in e["scores"] for c in LIVE):
            self.cache[title] = e
            return ({c: float(e["scores"][c]) for c in LIVE},
                    e.get("conf", "cache"))
        scores, conf, sources = self._search_and_score(title, author, genre)
        entry = {"scores": scores, "conf": conf, "sources": sources}
        with self._lock:
            self.cache[title] = entry
            rp.save_cache(self.cache, self.cache_path)
        rp.db_cache_put(self.cache_path, title, entry)
        return scores, conf

    def _search_and_score(self, title, author, genre, max_continuations=6):
        """One agentic Opus turn: search Goodreads, then emit the rubric JSON.
        Mirrors research_predict._web_search_json's pause_turn handling."""
        prompt = rm.rich_prompt(title, author, genre) + _GROUNDING
        client = self._client_lazy()

        def _call():
            messages = [{"role": "user", "content": prompt}]
            sources, resp = [], None
            for _ in range(max_continuations):
                resp = client.messages.create(
                    model=self.model, max_tokens=1500,
                    tools=[self.search_tool], messages=messages)
                for block in resp.content:
                    if getattr(block, "type", None) == "web_search_tool_result":
                        results = getattr(block, "content", None)
                        if isinstance(results, list):
                            for r in results:
                                u = getattr(r, "url", None)
                                if u:
                                    sources.append(u)
                if resp.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                break
            text = "".join(getattr(b, "text", "") for b in (resp.content if resp else [])
                           if getattr(b, "type", None) == "text")
            data = rl._extract_json(text)
            conf = data.pop("confidence", "unknown")
            scores = {c: float(data[c]) for c in LIVE if c in data}
            if len(scores) != len(LIVE):
                missing = [c for c in LIVE if c not in scores]
                raise ValueError(f"web researcher returned incomplete scores, "
                                 f"missing {missing}")
            seen, uniq = set(), []
            for u in sources:
                if u not in seen:
                    seen.add(u)
                    uniq.append(u)
            return scores, conf, uniq

        return _retry(_call)


def _retry(fn, max_retries=5):
    """Retry on 429/5xx with exponential backoff (pattern from ab_test)."""
    delay = 5
    for attempt in range(max_retries):
        try:
            return fn()
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 120)
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", 0) >= 500 and attempt < max_retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 120)
            else:
                raise


# ---------------------------------------------------------------------------
# Researcher B′ (STEP 4): STAGED web-grounded researcher. Same rubric, same
# goodreads tool, same _extract_json, same cache mechanism as WebGroundedResearcher
# — but the single agentic Opus search-and-score turn is split into two
# model-routed stages:
#   1. RETRIEVAL (RETRIEVAL_MODEL, cheap): web_search Goodreads, emit a factual
#      reader-evidence brief (rating, recurring praise/criticism, per-aspect
#      sentiment). Pure summarisation — it does NOT score.
#   2. SCORING (RESEARCH_MODEL = Opus, no tools): the validated rich_prompt
#      rubric + that evidence brief -> the 14 component scores. Opus judgement is
#      preserved; only the search/summarise work is offloaded to a cheaper model.
# Drop-in for evaluate_researcher (same .research interface). Its own cache file
# (STAGED_WEB_CACHE) so staged entries never mix with the Opus-agentic cache.
# ---------------------------------------------------------------------------
_RETRIEVAL_INSTRUCTIONS = """You are gathering reader EVIDENCE about one specific book from Goodreads, to hand to a separate scorer. Do NOT score, rate, or judge the book yourself — only report what readers actually say.

BOOK: "{title}" by {author}  (genre: {genre})

Use the web_search tool (it is restricted to goodreads.com) to find this book's Goodreads page and reviews. Compile a factual brief of the reader consensus, grounded ONLY in what the retrieved reviews report (never your own impression or the author's general reputation). Cover each aspect only where the reviews actually speak to it.

After searching, respond with ONLY a JSON object — no prose, no markdown:
{{"goodreads_rating": "average rating and rough number of ratings if visible, else ''",
  "praise": ["recurring things readers consistently praise"],
  "criticism": ["recurring things readers consistently criticise"],
  "by_aspect": {{"plot": "what reviews say about plot/structure", "pacing": "pacing / page-turner quality", "action": "action/tension", "ending": "how readers felt about the ending", "characters": "character depth and motivations", "emotional_impact": "emotional resonance", "prose": "sentence-level writing", "narration": "narrative voice / POV", "themes": "ideas / themes / how thought-provoking", "worldbuilding": "world depth, integration, originality (if applicable)"}},
  "found": true}}
If you could not find the book on Goodreads, return {{"found": false}} with empty fields."""

_SCORING_GROUNDING = """

GROUNDING — base every component score on the retrieved Goodreads reader evidence
below (what readers actually report), mapped onto the reader's scale via the
anchors above. Do NOT score from the author's general reputation or a prior
impression. If the evidence is thin on a component, reflect that with a
mid-scale score and lower confidence.

READER EVIDENCE (from Goodreads):
{evidence}

Respond with ONLY the JSON object described above (the {n} components + a
"confidence" key). No prose, no markdown."""


class StagedWebGroundedResearcher(WebGroundedResearcher):
    label = "staged"

    def __init__(self, key_path="apikey.txt", cache_path=STAGED_WEB_CACHE,
                 model=RESEARCH_MODEL, retrieval_model=RETRIEVAL_MODEL,
                 search_max_uses=SEARCH_MAX_USES):
        super().__init__(key_path=key_path, cache_path=cache_path, model=model,
                         search_max_uses=search_max_uses)
        self.retrieval_model = retrieval_model  # scoring stays on self.model (Opus)

    def _retrieve(self, title, author, genre):
        """STAGE 1 — cheap model + web_search -> (evidence dict, source urls).
        Mirrors research_predict._web_search_json's pause_turn handling, but the
        model only summarises; it never scores."""
        prompt = _RETRIEVAL_INSTRUCTIONS.format(title=title, author=author, genre=genre)
        client = self._client_lazy()

        def _call():
            messages = [{"role": "user", "content": prompt}]
            sources, resp = [], None
            for _ in range(6):
                resp = client.messages.create(
                    model=self.retrieval_model, max_tokens=1500,
                    tools=[self.search_tool], messages=messages)
                for block in resp.content:
                    if getattr(block, "type", None) == "web_search_tool_result":
                        results = getattr(block, "content", None)
                        if isinstance(results, list):
                            for r in results:
                                u = getattr(r, "url", None)
                                if u:
                                    sources.append(u)
                if resp.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                break
            text = "".join(getattr(b, "text", "") for b in (resp.content if resp else [])
                           if getattr(b, "type", None) == "text")
            try:
                evidence = rl._extract_json(text)
            except Exception:
                evidence = {"found": False, "raw": text[:2000]}
            seen, uniq = set(), []
            for u in sources:
                if u not in seen:
                    seen.add(u)
                    uniq.append(u)
            return evidence, uniq

        return _retry(_call)

    def _score(self, title, author, genre, evidence):
        """STAGE 2 — Opus + the validated rich rubric + the evidence brief ->
        the 14 component scores. No tools; pure scoring judgement."""
        evidence_text = json.dumps(evidence, indent=2, ensure_ascii=False)
        prompt = rm.rich_prompt(title, author, genre) + _SCORING_GROUNDING.format(
            evidence=evidence_text, n=len(LIVE))
        client = self._client_lazy()

        def _call():
            msg = client.messages.create(
                model=self.model, max_tokens=600,
                messages=[{"role": "user", "content": prompt}])
            text = "".join(getattr(b, "text", "") for b in msg.content
                           if getattr(b, "type", None) == "text").strip()
            data = rl._extract_json(text)
            conf = data.pop("confidence", "unknown")
            scores = {c: float(data[c]) for c in LIVE if c in data}
            if len(scores) != len(LIVE):
                missing = [c for c in LIVE if c not in scores]
                raise ValueError(f"staged scorer returned incomplete scores, "
                                 f"missing {missing}")
            return scores, conf

        return _retry(_call)

    def _search_and_score(self, title, author, genre, max_continuations=6):
        """Override: retrieve (cheap model) THEN score (Opus). Same return shape
        (scores, conf, sources) as the agentic parent, so research()/the cache/
        evaluate_researcher are all unchanged."""
        evidence, sources = self._retrieve(title, author, genre)
        scores, conf = self._score(title, author, genre, evidence)
        return scores, conf, sources


# ---------------------------------------------------------------------------
# Pre-warm the web cache concurrently (the only spend). evaluate_researcher
# then runs over cache hits, so the honesty test stays the reused code path.
# ---------------------------------------------------------------------------
def prewarm_web(researcher, sample, workers):
    todo = [b for _, b in sample.iterrows() if b["Book"] not in researcher.cache
            or not all(c in researcher.cache[b["Book"]].get("scores", {}) for c in LIVE)]
    if not todo:
        print("  Web cache already covers the whole sample — no spend needed.")
        return
    print(f"  Researching {len(todo)} books with web_search "
          f"({workers} workers)...\n")
    done = 0

    def _one(b):
        researcher.research(b["Book"], b["Author"], b["Genre"])
        return b["Book"]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, b): b["Book"] for b in todo}
        for fut in as_completed(futs):
            title = futs[fut]
            try:
                fut.result()
                done += 1
                print(f"    [{done}/{len(todo)}] grounded: {title[:54]}")
            except Exception as e:
                print(f"    ERROR grounding {title[:40]}: {e}")
    print()


# ---------------------------------------------------------------------------
# Per-component MAE — the validate_engine logic (mean |pred_c - actual_c|),
# restricted to the common set of books both researchers fully scored.
# ---------------------------------------------------------------------------
def signal_verdict(mae):
    """validate_engine's per-component signal bands (reused verbatim)."""
    return ("strong signal" if mae < 0.9 else
            "moderate" if mae < 1.15 else "weak / noisy")


def fully_scored_titles(researcher, sample):
    out = set()
    for _, b in sample.iterrows():
        e = researcher.cache.get(b["Book"])
        if e and all(c in e.get("scores", {}) for c in LIVE):
            out.add(b["Book"])
    return out


def per_component_mae(researcher, sample, common, gw):
    """{component: (mae, n)} over `common`, mirroring validate_engine's
    comp_err accumulation (skip None/NaN actuals). Worldbuilding components are
    skipped for genres where worldbuilding carries no weight (actual is a
    convention-0, not a real judgement), so they aren't measured as huge errors."""
    comp_err = {c: [] for c in LIVE}
    for _, b in sample.iterrows():
        if b["Book"] not in common:
            continue
        s = researcher.cache[b["Book"]]["scores"]
        wb_applies = (gw.get(b["Genre"], {}).get("Worldbuilding", 0) or 0) > 0
        for c in LIVE:
            if c in WB_COMPONENTS and not wb_applies:
                continue
            actual = b[c]
            if actual is None or (isinstance(actual, float) and np.isnan(actual)):
                continue
            comp_err[c].append(abs(float(s[c]) - float(actual)))
    return {c: (float(np.mean(v)), len(v)) for c, v in comp_err.items() if v}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-per-genre", type=int, default=3,
                    help="stratified sample size knob (default 3 = existing ~25-book sample)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=5, help="concurrent web calls")
    ap.add_argument("--yes", action="store_true",
                    help="authorize the live web-grounded spend")
    ap.add_argument("--no-loo", action="store_true",
                    help="skip the slow run_loo per-component tag")
    ap.add_argument("--out", default="compare_researchers_result.json")
    args = ap.parse_args()

    books, gw, gcw = db_loader.load_from_db()
    sample = rl.stratified_sample(books, n_per_genre=args.n_per_genre, seed=args.seed)

    print("=" * 78)
    print("RESEARCHER COMPARISON — memory-based vs web-grounded (per component)")
    print("=" * 78)
    print(f"  Sample: {len(sample)} books, {sample['Genre'].nunique()} genres "
          f"(stratified, seed={args.seed}, n_per_genre={args.n_per_genre})")
    print(f"  Model (both sides): {RESEARCH_MODEL}   |   only variable: web grounding")

    mem = MemoryResearcher()
    web = WebGroundedResearcher()

    # --- cost gate: count live web calls needed, estimate, confirm -----------
    todo = [b["Book"] for _, b in sample.iterrows()
            if b["Book"] not in web.cache
            or not all(c in web.cache[b["Book"]].get("scores", {}) for c in LIVE)]
    mem_missing = [b["Book"] for _, b in sample.iterrows() if b["Book"] not in mem.cache]
    print(f"\n  Memory side: {len(sample) - len(mem_missing)}/{len(sample)} cached "
          f"({len(mem_missing)} would need calls).")
    print(f"  Web side   : {len(sample) - len(todo)}/{len(sample)} cached "
          f"({len(todo)} live web_search calls needed).")
    if todo:
        est = len(todo) * EST_COST_PER_WEB_BOOK
        print(f"  Estimated live web spend: ~${est:.2f} "
              f"({len(todo)} books x ~${EST_COST_PER_WEB_BOOK:.2f}/book, Opus + searches).")
        if not args.yes:
            print("\n  >> No spend made. Re-run with --yes to authorize the live web run. <<")
            return

    # --- the spend: prewarm web cache concurrently --------------------------
    if todo:
        print("\n  Running live web-grounded research...")
        prewarm_web(web, sample, args.workers)

    # --- overall WA MAE via the REUSED honesty test (cache hits now) --------
    print("  Scoring WA through the honesty test (rl.evaluate_researcher)...")
    df_mem = rl.evaluate_researcher(books, gw, gcw, mem, sample, verbose=False)
    df_web = rl.evaluate_researcher(books, gw, gcw, web, sample, verbose=False)

    # --- common set: books BOTH researchers fully scored --------------------
    common = fully_scored_titles(mem, sample) & fully_scored_titles(web, sample)
    dropped = len(sample) - len(common)
    if dropped:
        print(f"  Note: {dropped} book(s) dropped (not fully scored by both).")

    wa_mem = float(df_mem[df_mem["Book"].isin(common)]["err"].mean())
    wa_web = float(df_web[df_web["Book"].isin(common)]["err"].mean())

    # --- per-component MAE (validate_engine logic) on the common set --------
    mae_mem = per_component_mae(mem, sample, common, gw)
    mae_web = per_component_mae(web, sample, common, gw)

    # --- step 3 tag: per-component LOO MAE from validate_engine -------------
    loo_mae = {}
    if not args.no_loo:
        print("  Running validate_engine.run_loo() for the per-component "
              "well-predicted/noisy tag (slow)...")
        loo = ve.run_loo(books=books, gw=gw, gcw=gcw)
        loo_mae = {r["component"]: r["mae"] for r in loo["per_component"]}

    # --- assemble rows, sorted by improvement (delta = memory - grounded) ---
    rows = []
    for c in LIVE:
        if c not in mae_mem or c not in mae_web:
            continue
        mm, n = mae_mem[c]
        wm, _ = mae_web[c]
        delta = mm - wm  # positive => grounding LOWERS MAE => grounding helps
        verdict = ("grounding helps" if delta > EPS else
                   "grounding HURTS" if delta < -EPS else "no change")
        rows.append({
            "component": c, "n": n,
            "memory_mae": round(mm, 4), "grounded_mae": round(wm, 4),
            "delta": round(delta, 4), "verdict": verdict,
            "loo_mae": (round(loo_mae[c], 4) if c in loo_mae else None),
            "signal": (signal_verdict(loo_mae[c]) if c in loo_mae else None),
        })
    rows.sort(key=lambda r: r["delta"], reverse=True)

    _print_table(rows, wa_mem, wa_web, len(common))
    _print_trust_split(rows)

    result = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "model": RESEARCH_MODEL,
        "sample_size": len(sample), "n_common": len(common),
        "n_per_genre": args.n_per_genre, "seed": args.seed,
        "wa_mae": {"memory": round(wa_mem, 4), "grounded": round(wa_web, 4),
                   "delta": round(wa_mem - wa_web, 4)},
        "components": rows,
        "trust_crowd": [r["component"] for r in rows if r["delta"] > EPS],
        "trust_analogs": [r["component"] for r in rows if r["delta"] < -EPS],
        "neutral": [r["component"] for r in rows if abs(r["delta"]) <= EPS],
    }
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Wrote {args.out}")


def _print_table(rows, wa_mem, wa_web, n_common):
    print("\n" + "-" * 78)
    print(f"PER-COMPONENT MAE  (n={n_common} common books, blind)   "
          f"sorted by improvement")
    print("-" * 78)
    print(f"  {'Component':<22}{'mem MAE':>9}{'web MAE':>9}{'delta':>8}"
          f"{'verdict':>18}{'LOO':>7}")
    print("  " + "-" * 74)
    for r in rows:
        loo = f"{r['loo_mae']:.2f}" if r["loo_mae"] is not None else "  -"
        print(f"  {r['component']:<22}{r['memory_mae']:>9.3f}{r['grounded_mae']:>9.3f}"
              f"{r['delta']:>+8.3f}{r['verdict']:>18}{loo:>7}")
    print("  " + "-" * 74)
    print(f"  {'OVERALL WA MAE':<22}{wa_mem:>9.3f}{wa_web:>9.3f}"
          f"{wa_mem - wa_web:>+8.3f}"
          f"{('grounding helps' if wa_mem - wa_web > EPS else 'grounding HURTS' if wa_mem - wa_web < -EPS else 'no change'):>18}")
    print("\n  delta = memory MAE - grounded MAE.  Positive => web-grounding "
          "lowers error (helps).")
    print("  LOO = validate_engine per-component leave-one-out MAE "
          "(lower = already well-predicted).")
    print("  Worldbuilding (Depth2/Integration/Originality) measured only on "
          "genres where it carries weight.")


def _print_trust_split(rows):
    crowd = [r for r in rows if r["delta"] > EPS]
    analogs = [r for r in rows if r["delta"] < -EPS]
    neutral = [r for r in rows if abs(r["delta"]) <= EPS]
    print("\n" + "=" * 78)
    print("TRUST-THE-CROWD vs TRUST-THE-READER  (computed per-component recommendation)")
    print("=" * 78)
    print("\n  TRUST THE CROWD — web-ground these (grounding lowers MAE):")
    if crowd:
        for r in crowd:
            print(f"    + {r['component']:<22} grounded {r['grounded_mae']:.3f} vs "
                  f"memory {r['memory_mae']:.3f}  ({r['delta']:+.3f})")
    else:
        print("    (none — grounding did not clearly lower MAE on any component)")
    print("\n  TRUST THE READER'S OWN ANALOGS — do NOT web-ground these "
          "(grounding raises MAE):")
    if analogs:
        for r in analogs:
            print(f"    - {r['component']:<22} grounded {r['grounded_mae']:.3f} vs "
                  f"memory {r['memory_mae']:.3f}  ({r['delta']:+.3f})")
    else:
        print("    (none — grounding did not clearly raise MAE on any component)")
    if neutral:
        print(f"\n  NEUTRAL (|delta| <= {EPS}): "
              + ", ".join(r["component"] for r in neutral))


if __name__ == "__main__":
    main()
