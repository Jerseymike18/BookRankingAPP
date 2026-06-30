"""
hybrid_researcher.py
====================
A per-component HYBRID researcher: each of the 14 components is sourced from
whichever method the blind comparison favored — instead of all-memory or
all-web-grounded.

WHY (from the n=32 blind evaluate_researcher comparison, compare_researchers.py;
overall WA MAE memory 0.882 vs web-grounded 0.816):
  - Web-grounding HELPS externally-describable substance — Originality, Insights,
    Depth2, Integration, Depth, Ending. (The worldbuilding dims also had the
    worst baselines, so grounding helps most where memory was weakest.)
  - Web-grounding HURTS irreducibly personal reactions — Narration and
    Thought-Provokingness. These were the ONLY two components that regressed in
    BOTH the n=15 and n=32 runs, so that is the trustworthy signal.
  - Everything else was neutral (within noise). With no measured benefit and a
    real cost+latency to web search, neutral components default to memory.

DESIGN
  - Reuses the existing MemoryResearcher and WebGroundedResearcher (and their
    caches: llm_scores_richer.json + web_grounded_cache.json) — no new LLM path.
  - Runs the web search AT MOST ONCE per book (the grounded call already returns
    all 14; we just keep the policy-assigned subset of it).
  - Returns the 14 numbers through the SAME research()/evaluate_researcher
    interface, so the validation harness and the downstream math are unchanged.
    NO prediction/derived-math change — only how each component is SOURCED.

VERIFY / RUN
  python hybrid_researcher.py          # blind comparison vs both pure baselines
  python hybrid_researcher.py --yes    # authorize web calls if the cache is cold
"""

import argparse
import datetime as _dt
import json
from concurrent.futures import ThreadPoolExecutor

import db_loader
import research_layer as rl
import compare_researchers as cr

LIVE = cr.LIVE  # the 14 components, reference order

# ---------------------------------------------------------------------------
# THE POLICY — component -> "grounded" | "memory". Editable single source of
# truth. Seeded from the n=32 blind comparison (deltas = memory MAE - grounded
# MAE; positive = grounding lowered error). Re-derive from compare_researchers.py
# and edit this dict as n grows and the picture sharpens.
# ---------------------------------------------------------------------------
SOURCING_POLICY = {
    # GROUNDED — consistent / clear per-component gains from web reviews
    "Originality":           "grounded",  # +0.212
    "Insights":              "grounded",  # +0.147
    "Depth2":                "grounded",  # +0.131
    "Ending":                "grounded",  # +0.125
    "Integration":           "grounded",  # +0.119
    "Depth":                 "grounded",  # +0.087
    # MEMORY — grounding HURT in BOTH the n=15 and n=32 runs (trustworthy)
    "Narration":             "memory",    # -0.094
    "Thought-Provokingness": "memory",    # -0.097
    # NEUTRAL (within noise) — no measured benefit; default to cheaper memory
    "Emotional Impact":      "memory",    # +0.047
    "Motivations":           "memory",    # +0.028
    "Action":                "memory",    # -0.003
    "Prose":                 "memory",    # -0.019
    "Plot":                  "memory",    # -0.041
    "Entertainment":         "memory",    # -0.044
}


def grounded_components(policy=None):
    p = policy or SOURCING_POLICY
    return {c for c in LIVE if p.get(c) == "grounded"}


# ---------------------------------------------------------------------------
# The hybrid researcher — drop-in for evaluate_researcher (.research interface)
# ---------------------------------------------------------------------------
class HybridResearcher:
    label = "hybrid"

    def __init__(self, key_path="apikey.txt", policy=None,
                 memory=None, web=None):
        self.policy = dict(policy or SOURCING_POLICY)
        self.memory = memory or cr.MemoryResearcher(key_path=key_path)
        self.web = web or cr.WebGroundedResearcher(key_path=key_path)
        self.cache = {}   # title -> {"scores", "conf"} (assembled; harness-compatible)
        self.meta = {}    # title -> per-component source + confidences + web sources
        self._needs_web = bool(grounded_components(self.policy))

    def research(self, title, author, genre):
        """Return ({component: score} for all 14, conf_string). Grounded
        components come from the web researcher, the rest from memory; the web
        search runs at most once (and only if some component is grounded)."""
        # STEP 1 — cache short-circuit (before ANY sub-researcher call). A title
        # already fully assembled in this researcher's cache returns instantly.
        # The sub-researchers also short-circuit on their own caches (so an
        # uncached-here-but-cached-there book still makes zero API calls); this
        # makes the hybrid's own assembled cache load-bearing too, so a repeat
        # research() of the same book is a pure dict hit.
        cached = self.cache.get(title)
        if cached and all(c in cached.get("scores", {}) for c in LIVE):
            return ({c: float(cached["scores"][c]) for c in LIVE},
                    cached.get("conf", "cache"))

        # STEP 2 — fire the memory call and the web retrieval CONCURRENTLY. They
        # are independent (the web search needs no memory output), so running them
        # in parallel makes wall-clock max(mem, web) instead of mem + web. Same
        # calls, same results — only the ordering changes; cached sub-researcher
        # calls return instantly, so this is a no-op when both are warm.
        if self._needs_web:
            with ThreadPoolExecutor(max_workers=2) as ex:
                mem_fut = ex.submit(self.memory.research, title, author, genre)
                web_fut = ex.submit(self.web.research, title, author, genre)
                mem_scores, mem_conf = mem_fut.result()
                web_scores, web_conf = web_fut.result()
            sources = self.web.cache.get(title, {}).get("sources", [])
        else:
            mem_scores, mem_conf = self.memory.research(title, author, genre)
            web_scores, web_conf, sources = {}, None, []

        scores, source_of = {}, {}
        for c in LIVE:
            want = self.policy.get(c, "memory")
            primary = web_scores if want == "grounded" else mem_scores
            fallback = mem_scores if want == "grounded" else web_scores
            if c in primary:
                scores[c] = float(primary[c])
                source_of[c] = want
            elif c in fallback:  # defensive: missing from chosen source
                scores[c] = float(fallback[c])
                source_of[c] = "memory" if want == "grounded" else "grounded"
            # else: leave missing; evaluate_researcher will skip the book

        conf = (f"web:{web_conf}/mem:{mem_conf}" if self._needs_web
                else f"mem:{mem_conf}")
        self.cache[title] = {"scores": scores, "conf": conf}
        self.meta[title] = {"source_of": source_of, "web_conf": web_conf,
                            "mem_conf": mem_conf, "sources": sources}
        return scores, conf


# ---------------------------------------------------------------------------
# Production hook: override a memory score dict's grounded components with
# web-grounded values. Used by the single-book predict flow so it sources each
# component per the policy. Sourcing only — no prediction/derived-math change.
#
# Default ON because the n=32 verification had hybrid (0.810) <= both pure memory
# (0.882) and pure grounded (0.816). Flip to False to disable grounding entirely.
# NOTE: a grounded upgrade adds ONE web_search call (~110s, ~$0.12) per *uncached*
# book; cached books (web_grounded_cache.json) are free. The predict endpoint
# applies it ONLY when the caller passes grounded=True, so the candidate list
# scores instantly with memory and each book is refined to the hybrid in the
# background (progressive) — never N blocking web calls up front.
# ---------------------------------------------------------------------------
HYBRID_SOURCING_DEFAULT = True

# STEP 4 — when True, the production grounded upgrade uses the STAGED researcher
# (cheap-model retrieval -> Opus scoring) instead of the single Opus agentic
# search-and-score turn. OFF until a live evaluate_researcher A/B confirms WA MAE
# does not regress vs the Opus-agentic baseline (~0.81). Flip to True ONLY after
# that verification — one line, fully revertible. The staged researcher keeps its
# own cache (cr.STAGED_WEB_CACHE), so enabling this never reads or pollutes the
# Opus-agentic web_grounded_cache.json.
STAGED_RETRIEVAL_DEFAULT = False

_SHARED_WEB = None


def _shared_web(key_path="apikey.txt"):
    """Lazily build (once) the process-wide grounded researcher used by the
    production predict path — staged or Opus-agentic per STAGED_RETRIEVAL_DEFAULT."""
    global _SHARED_WEB
    if _SHARED_WEB is None:
        _SHARED_WEB = (cr.StagedWebGroundedResearcher(key_path=key_path)
                       if STAGED_RETRIEVAL_DEFAULT
                       else cr.WebGroundedResearcher(key_path=key_path))
    return _SHARED_WEB


def apply_grounded_overrides(title, author, genre, mem_scores,
                             web=None, key_path="apikey.txt", policy=None):
    """Return a COPY of mem_scores with the policy's grounded components replaced
    by web-grounded values (one cached web call); memory keeps everything else.
    Reuses the shared grounded researcher + its cache. Raises on web failure so
    the caller can fall back to the untouched memory scores."""
    if web is None:
        web = _shared_web(key_path)
    web_scores, _conf = web.research(title, author, genre)
    out = dict(mem_scores)
    for c in grounded_components(policy):
        if c in web_scores:
            out[c] = float(web_scores[c])
    return out


# ---------------------------------------------------------------------------
# Verification: hybrid vs BOTH pure baselines, same blind sample (step 4)
# ---------------------------------------------------------------------------
def _wa_mae(df, common):
    return float(df[df["Book"].isin(common)]["err"].mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-per-genre", type=int, default=3,
                    help="stratified sample knob (default 3 = the existing sample)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--yes", action="store_true",
                    help="authorize web spend if the web cache is cold")
    ap.add_argument("--out", default="hybrid_researcher_result.json")
    args = ap.parse_args()

    books, gw, gcw = db_loader.load_from_db()
    sample = rl.stratified_sample(books, n_per_genre=args.n_per_genre, seed=args.seed)

    # One shared memory + web researcher, reused by the hybrid and the baselines,
    # so caches and any live calls are never duplicated.
    hybrid = HybridResearcher()
    mem, web = hybrid.memory, hybrid.web

    print("=" * 78)
    print("HYBRID RESEARCHER — verification vs pure memory and pure web-grounded")
    print("=" * 78)
    g = sorted(grounded_components())
    print(f"  Sample: {len(sample)} books (stratified, seed={args.seed}, "
          f"n_per_genre={args.n_per_genre})")
    print(f"  Policy: {len(g)} grounded {g}")
    print(f"          {len(LIVE) - len(g)} memory "
          f"{sorted(set(LIVE) - set(g))}")

    # --- cost gate: hybrid needs web scores for the grounded components --------
    todo = [b["Book"] for _, b in sample.iterrows()
            if b["Book"] not in web.cache
            or not all(c in web.cache[b["Book"]].get("scores", {}) for c in LIVE)]
    if todo:
        est = len(todo) * cr.EST_COST_PER_WEB_BOOK
        print(f"\n  Web cache missing {len(todo)} books; live spend ~${est:.2f}.")
        if not args.yes:
            print("  >> No spend made. Re-run with --yes to authorize. <<")
            return
    else:
        print("\n  Web + memory caches already cover the sample — free run.")

    # --- WA MAE for all three through the SAME honesty test --------------------
    print("  Scoring all three researchers (rl.evaluate_researcher)...")
    df_mem = rl.evaluate_researcher(books, gw, gcw, mem, sample, verbose=False)
    df_web = rl.evaluate_researcher(books, gw, gcw, web, sample, verbose=False)
    df_hyb = rl.evaluate_researcher(books, gw, gcw, hybrid, sample, verbose=False)

    common = (cr.fully_scored_titles(mem, sample)
              & cr.fully_scored_titles(web, sample)
              & cr.fully_scored_titles(hybrid, sample))
    n = len(common)

    wa = {"memory": _wa_mae(df_mem, common),
          "grounded": _wa_mae(df_web, common),
          "hybrid": _wa_mae(df_hyb, common)}

    # --- per-component MAE for all three (validate_engine logic, reused) -------
    pc = {"memory": cr.per_component_mae(mem, sample, common, gw),
          "grounded": cr.per_component_mae(web, sample, common, gw),
          "hybrid": cr.per_component_mae(hybrid, sample, common, gw)}

    _print_report(wa, pc, n)

    beats_grounding = wa["hybrid"] <= wa["grounded"] + 1e-9
    beats_memory = wa["hybrid"] <= wa["memory"] + 1e-9
    # Guard: the two protected components must not regress toward grounded.
    protected_ok = all(
        c not in pc["hybrid"] or c not in pc["memory"]
        or abs(pc["hybrid"][c][0] - pc["memory"][c][0]) < 1e-6
        for c in ("Narration", "Thought-Provokingness"))

    result = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "n_common": n, "sample_size": len(sample),
        "policy": SOURCING_POLICY,
        "wa_mae": {k: round(v, 4) for k, v in wa.items()},
        "beats_grounding": beats_grounding, "beats_memory": beats_memory,
        "protected_components_held": protected_ok,
        "per_component": {
            c: {src: round(pc[src][c][0], 4) for src in pc if c in pc[src]}
            for c in LIVE},
    }
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Wrote {args.out}")
    return result


def _print_report(wa, pc, n):
    print("\n" + "-" * 78)
    print(f"PER-COMPONENT MAE  (n={n} common books, blind)")
    print("-" * 78)
    print(f"  {'Component':<22}{'source':>10}{'memory':>9}{'grounded':>10}"
          f"{'hybrid':>9}")
    print("  " + "-" * 74)
    for c in LIVE:
        src = SOURCING_POLICY.get(c, "memory")
        m = pc["memory"].get(c, (None,))[0]
        w = pc["grounded"].get(c, (None,))[0]
        h = pc["hybrid"].get(c, (None,))[0]
        def f(x):
            return f"{x:.3f}" if x is not None else "  -"
        mark = "  <-" if src == "grounded" else ""
        print(f"  {c:<22}{src:>10}{f(m):>9}{f(w):>10}{f(h):>9}{mark}")
    print("  " + "-" * 74)

    print("\n" + "=" * 78)
    print("OVERALL WA MAE  (the real test — does the mix beat both pure methods?)")
    print("=" * 78)
    print(f"  pure memory   : {wa['memory']:.4f}")
    print(f"  pure grounded : {wa['grounded']:.4f}")
    print(f"  HYBRID        : {wa['hybrid']:.4f}")
    dvg = wa["grounded"] - wa["hybrid"]
    dvm = wa["memory"] - wa["hybrid"]
    print(f"\n  hybrid vs grounded: {dvg:+.4f}  "
          f"({'beats' if dvg >= -1e-9 else 'WORSE than'} pure grounding)")
    print(f"  hybrid vs memory  : {dvm:+.4f}  "
          f"({'beats' if dvm >= -1e-9 else 'WORSE than'} pure memory)")
    if wa["hybrid"] <= wa["grounded"] + 1e-9 and wa["hybrid"] <= wa["memory"] + 1e-9:
        print("\n  => HYBRID WINS (<= both). Eligible to become the default researcher.")
    elif wa["hybrid"] <= wa["grounded"] + 1e-9:
        print("\n  => Hybrid beats pure grounding (the stronger baseline). Eligible.")
    else:
        print("\n  => Hybrid does NOT beat pure grounding. Do NOT switch the default;")
        print("     keep it available but non-default and report this honestly.")


if __name__ == "__main__":
    main()
