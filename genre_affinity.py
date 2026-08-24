"""genre_affinity.py — what the reader's OWN library says about GENRES.

The Predict page has always been able to answer "how much will I like THIS book".
It could never answer "what should I read MORE of" — Discover's candidate
generator sees the reader's titles (to avoid them) and their genre list (to copy
spellings from), but not one number about how they actually rate genres. Genre
selection was whatever the reader typed. This module is the missing evidence.

TWO HALVES, DELIBERATELY SEPARATED
----------------------------------
1. ``genre_evidence`` — pure, deterministic, zero-API. Reads the reader's rated
   library (+ their delta_log and TBR) and returns per-genre evidence. No LLM
   anywhere near it, so it is testable and cannot hallucinate.
2. ``recommend_genres`` — ONE LLM call that reads that evidence and argues for a
   few genres. It never computes; it only reads numbers this module produced.

WHY IT IS NOT JUST "AVERAGE WA BY GENRE"
----------------------------------------
Because that ranking is mostly noise. On the reference library it puts Russian
Literature (n=3) and Gothic Fiction (n=2) above everything, on 2 and 3 books.
Three things fix it, and each is load-bearing:

* **Shrinkage + a band.** Every genre mean is shrunk toward the library mean by
  an empirical-Bayes constant fitted from the library's own between/within-genre
  spread, and carries an 80% band. A thin genre keeps its high estimate but shows
  a band 4x wider than a well-evidenced one. Nothing is hidden and nothing is
  invented — same discipline as the omitted conformal interval.
* **Signed surprise, not MAE.** ``track_record`` already reports |error| by genre
  (how RELIABLE the engine is there). The recommender needs the DIRECTION: a
  genre the engine systematically UNDER-predicts is one the reader enjoys more
  than the model expects, which is the single most actionable thing in here and
  is invisible in both avg WA and MAE. See ``engine_forecast_rows`` for which
  rows count and why this one deliberately differs from the Track Record's.
* **Volume is not affinity.** The dominant genre wins any raw ranking by weight
  of numbers ("read more Epic Fantasy" — 42% of the reference library). Read
  share is reported SEPARATELY from affinity so the recommender can say "you read
  a lot of this" and "you rate it highly" as two different claims.

THE WORLDBUILDING MASK — a real trap, not a nicety
--------------------------------------------------
Worldbuilding is optional (scored 0) for realist genres. A naive component
z-profile therefore reports Literary Fiction at -2.26 on Depth2 / Integration /
Originality, which reads as "the reader hates its worldbuilding" when it actually
means "that genre has no worldbuilding to score". Those three components are
masked to None for any genre whose Worldbuilding weight is 0. See ``_WB_COMPS``.

READ-ONLY. This module CONSUMES the engine (``predict_engine`` / ``db_loader`` /
``views``) and never reimplements or mutates any of it — same standing as
``track_record.py``, ``intervals.py`` and ``delta_log_view.py``. It computes no
prediction, writes nothing, and touches no scoring math.
"""
from __future__ import annotations

import math
import re
import statistics

import delta_log_view
import research_layer as rl

# One LLM call, and it is pure narration over numbers this module computed —
# no calibration benefit from Opus, so it takes the cheap model like Discover's
# other generation step (see CLAUDE.md "LLM model usage").
GENRE_MODEL = "claude-sonnet-4-6"

# The 14 components, in the CLAUDE.md category order.
COMPONENTS = [
    "Plot", "Entertainment", "Action", "Ending",
    "Depth", "Emotional Impact", "Motivations",
    "Prose", "Narration",
    "Insights", "Thought-Provokingness",
    "Depth2", "Integration", "Originality",
]

# Scored 0 for realist genres — see the module docstring's mask note.
_WB_COMPS = ("Depth2", "Integration", "Originality")

# 80% band, matching the served conformal interval's coverage choice (owner
# decision 2026-07-07). This is NOT that interval and must never be presented as
# one: this is an interval on a GENRE's mean rating, not on an unread book's
# predicted WA.
_Z80 = 1.2816

# Shrinkage floor. tau^2 is estimated from the library, but a library whose
# genres happen to sit close together yields a tiny tau and therefore an enormous
# shrinkage constant, flattening every genre onto the library mean. Capping k at
# MAX_K books keeps a genuinely thin genre visible instead of erased.
MAX_K = 25.0

# A genre needs this many books before its own mean is called well-evidenced.
# Not a cliff — the shrinkage is smooth and applies at every n; this only labels
# the evidence tier the prompt reads.
WELL_EVIDENCED_N = 8

# Minimum finished predictions in a genre before its signed surprise is worth
# reporting at all. Below this the mean of 1 error is not a bias, it is one book.
MIN_SURPRISE_N = 2

# Below this many rated books there is no genre evidence worth arguing from —
# every genre is a handful of books and the shrinkage flattens them all onto the
# library mean anyway. Deliberately the SAME threshold the cold-start model uses
# to decide a tenant has enough own data to stop borrowing the seed model
# (ARCHITECTURE.md, "cold-start v1"), so the app has one answer to "is this
# reader's library big enough to speak for itself" rather than two.
MIN_LIBRARY_BOOKS = 15


def _mean(xs):
    xs = [float(x) for x in xs if x is not None and not _isnan(x)]
    return sum(xs) / len(xs) if xs else None


def _isnan(v):
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def _round(v, nd=3):
    return None if v is None else round(float(v), nd)


# ---------------------------------------------------------------------------
# Empirical-Bayes shrinkage over genre means
# ---------------------------------------------------------------------------
def _shrinkage(groups, mu):
    """Fit the EB shrinkage constant from the library's own spread.

    `groups` is {genre: [WA, ...]}. Returns (within_var, tau2, k) where `k` is
    the shrinkage strength expressed IN BOOKS: a genre with n books is pulled
    k/(n+k) of the way to the library mean `mu`.

    within_var is pooled across genres with >=2 books; tau2 is the between-genre
    variance left AFTER removing the sampling noise that within-genre variance
    alone would produce (the DerSimonian-Laird move). A library whose genre means
    differ no more than sampling noise predicts yields tau2 -> floor -> maximum
    shrinkage, which is the right answer: there is no genre signal to report.
    """
    multi = [v for v in groups.values() if len(v) >= 2]
    if multi:
        within_var = _mean([statistics.variance(v) for v in multi]) or 0.0
    else:
        within_var = 0.0
    means = [_mean(v) for v in groups.values() if v]
    if len(means) >= 2 and within_var > 0:
        raw_between = statistics.variance(means)
        mean_inv_n = _mean([1.0 / len(v) for v in groups.values() if v]) or 1.0
        tau2 = raw_between - within_var * mean_inv_n
    else:
        tau2 = 0.0
    # Floor tau2 so k stays finite and bounded (see MAX_K).
    floor = within_var / MAX_K if within_var > 0 else 1e-6
    tau2 = max(tau2, floor)
    k = within_var / tau2 if tau2 > 0 else MAX_K
    return within_var, tau2, min(k, MAX_K)


# ---------------------------------------------------------------------------
# The evidence builder — pure, deterministic, zero-API
# ---------------------------------------------------------------------------
def genre_evidence(books, delta_rows=None, book_meta=None, genre_weights=None,
                   tbr_counts=None, allowed_genres=None):
    """Per-genre evidence from the reader's own library. No LLM, no writes.

    Args:
      books:        the engine's rated-library DataFrame (`db_loader.load_from_db`
                    output, or `views.add_total_average` of it). Needs at minimum
                    Book / Genre / WA, and the 14 component columns for profiles.
      delta_rows:   already-deduped delta_log rows (`delta_log_view.visible_rows`)
                    for signed surprise. Omit and the surprise block is absent —
                    never invented.
      book_meta:    {normalized title: {"genre": ...}} fallback for a delta row
                    with no `pred_genre`, exactly as /api/track-record supplies.
      genre_weights: {genre: {"Worldbuilding": w, ...}} — drives the WB mask.
      tbr_counts:   {genre: open recommendation count}.
      allowed_genres: the reader's full schema list. Genres in it with ZERO rated
                    books come back as evidence-free entries (`status="unread"`),
                    so the recommender can see the door exists without being handed
                    a mean that does not exist.

    Returns a dict: {"library": {...}, "genres": [...], "provenance": {...}}.
    """
    rows = _library_rows(books)
    if not rows:
        return {"library": {"n_books": 0}, "genres": [], "provenance": {}}

    groups = {}
    for r in rows:
        groups.setdefault(r["genre"], []).append(r["wa"])

    all_wa = [r["wa"] for r in rows]
    mu = _mean(all_wa)
    within_var, tau2, k = _shrinkage(groups, mu)
    n_books = len(rows)

    surprise = _surprise_by_genre(delta_rows, book_meta)
    profiles = _component_profiles(rows, genre_weights)
    years = _year_shares(rows)
    tbr = dict(tbr_counts or {})

    genres = []
    for genre, was in groups.items():
        n = len(was)
        raw = _mean(was)
        shrunk = (n * raw + k * mu) / (n + k)
        se = math.sqrt(within_var / (n + k)) if within_var > 0 else 0.0
        s = surprise.get(genre)
        genres.append({
            "genre": genre,
            "status": "read",
            "n_books": n,
            "read_share": _round(n / n_books, 4),
            "raw_mean_wa": _round(raw),
            # The affinity number to rank on. Shrunk toward the library mean by
            # k books of prior, so a 2-book genre cannot out-rank a 59-book one
            # on noise alone.
            "affinity": _round(shrunk),
            "band_low": _round(shrunk - _Z80 * se),
            "band_high": _round(shrunk + _Z80 * se),
            "band_width": _round(2 * _Z80 * se),
            "vs_library": _round(shrunk - mu),
            "best_wa": _round(max(was)),
            "worst_wa": _round(min(was)),
            "evidence": ("strong" if n >= WELL_EVIDENCED_N
                         else "thin" if n >= MIN_SURPRISE_N else "single-book"),
            # Signed: positive = the engine UNDER-predicts this genre for this
            # reader (they enjoy it more than the model expects). None when the
            # genre has too few finished predictions to mean anything.
            "surprise": s,
            "profile": profiles.get(genre, {}),
            "year_share": years.get(genre, {}),
            "tbr_open": int(tbr.get(genre, 0)),
        })

    # Schema genres with no rated books: real options, zero own evidence.
    for genre in sorted(set(allowed_genres or ()) - set(groups)):
        genres.append({
            "genre": genre,
            "status": "unread",
            "n_books": 0,
            "read_share": 0.0,
            "raw_mean_wa": None, "affinity": None,
            "band_low": None, "band_high": None, "band_width": None,
            "vs_library": None, "best_wa": None, "worst_wa": None,
            "evidence": "none",
            "surprise": None, "profile": {}, "year_share": {},
            "tbr_open": int(tbr.get(genre, 0)),
        })

    genres.sort(key=lambda g: (g["status"] != "read",
                               -(g["affinity"] if g["affinity"] is not None else -99),
                               g["genre"]))

    return {
        "library": {
            "n_books": n_books,
            "n_genres_read": len(groups),
            "mean_wa": _round(mu),
            "within_genre_sd": _round(math.sqrt(within_var)) if within_var > 0 else None,
            "shrinkage_k_books": _round(k, 2),
            "drivers": _drivers(rows),
        },
        "genres": genres,
        "provenance": {
            "affinity": "empirical-Bayes shrunk genre mean WA, 80% band",
            "surprise": ("signed mean of (actual - predicted) WA over finished "
                         "predictions, from delta_log"),
            "surprise_rows": sum(1 for g in genres if g.get("surprise")),
            "has_delta_log": bool(delta_rows),
        },
    }


def _library_rows(books):
    """Normalize the engine DataFrame into plain dicts (title/genre/wa/year/comps).

    Deliberately tolerant: this module is handed the engine's frame in the app and
    a hand-built one in tests, so it reads by column name and skips a row missing
    the two fields everything depends on."""
    out = []
    try:
        records = books.to_dict("records")
    except AttributeError:
        records = list(books)
    for r in records:
        genre = str(r.get("Genre") or "").strip()
        wa = r.get("WA")
        if not genre or wa is None or _isnan(wa):
            continue
        comps = {}
        for c in COMPONENTS:
            v = r.get(c)
            if v is not None and not _isnan(v):
                comps[c] = float(v)
        year = r.get("Year")
        try:
            year = int(year) if year is not None and not _isnan(year) else None
        except (TypeError, ValueError):
            year = None
        out.append({"title": str(r.get("Book") or "").strip(),
                    "genre": genre, "wa": float(wa), "year": year,
                    "comps": comps})
    return out


def _drivers(rows):
    """What separates this reader's best books from their worst, component-wise.

    Top-quartile mean minus bottom-quartile mean, per component, biggest gap
    first. This is the evidence the "types of books" half of the recommendation
    rests on: it describes what the reader responds to WITHOUT reference to any
    genre label, which is exactly what a sub-genre hypothesis needs.

    Note it is descriptive, not causal — WA is computed FROM these components, so
    a large gap partly reflects the component's weight and spread. It is read as
    "your best books look like this", never as "raise this and you'll enjoy more".
    """
    if len(rows) < 8:
        return []
    was = sorted(r["wa"] for r in rows)
    q1 = was[len(was) // 4]
    q3 = was[(3 * len(was)) // 4]
    top = [r for r in rows if r["wa"] >= q3]
    bot = [r for r in rows if r["wa"] <= q1]
    out = []
    for c in COMPONENTS:
        t = _mean([r["comps"].get(c) for r in top])
        b = _mean([r["comps"].get(c) for r in bot])
        if t is None or b is None:
            continue
        out.append({"component": c, "top_quartile": _round(t, 2),
                    "bottom_quartile": _round(b, 2), "gap": _round(t - b, 2)})
    out.sort(key=lambda d: -(d["gap"] or 0))
    return out


def _component_profiles(rows, genre_weights):
    """Per-genre component z-profile vs the library, with the worldbuilding mask.

    A genre whose Worldbuilding weight is 0 gets None for the three WB components
    rather than the large negative z their structural zeros would produce. When no
    weights are supplied the mask falls back to the data itself: a genre whose
    every book scores 0 on all three is treated as realist. Never guessed further
    than that — a partial pattern is left alone."""
    stats = {}
    for c in COMPONENTS:
        vals = [r["comps"].get(c) for r in rows if r["comps"].get(c) is not None]
        if len(vals) >= 2:
            sd = statistics.pstdev(vals)
            stats[c] = (_mean(vals), sd if sd > 1e-9 else None)
    by_genre = {}
    for r in rows:
        by_genre.setdefault(r["genre"], []).append(r)
    out = {}
    for genre, grows in by_genre.items():
        masked = _wb_masked(genre, grows, genre_weights)
        prof = {}
        for c in COMPONENTS:
            if c in _WB_COMPS and masked:
                prof[c] = None            # not applicable, NOT a low score
                continue
            m, sd = stats.get(c, (None, None))
            gm = _mean([r["comps"].get(c) for r in grows])
            prof[c] = _round((gm - m) / sd, 2) if (gm is not None and m is not None
                                                   and sd) else None
        out[genre] = prof
    return out


def _wb_masked(genre, grows, genre_weights):
    """True when worldbuilding does not apply to this genre."""
    if genre_weights:
        w = (genre_weights.get(genre) or {}).get("Worldbuilding")
        if w is not None:
            return float(w) == 0.0
    vals = [r["comps"].get(c) for r in grows for c in _WB_COMPS]
    vals = [v for v in vals if v is not None]
    return bool(vals) and all(v == 0 for v in vals)


def engine_forecast_rows(entries, finished_titles, backfill_marker):
    """The delta_log rows whose prediction an ENGINE produced — the surprise set.

    Deliberately NOT the same set the Track Record shows, and the difference is
    the whole point of this function.

    ``delta_log_view.visible_rows`` prefers, per book, live > workbook-backfill >
    retro_sweep. That ordering is right for the Track Record, which asks "what did
    this reader actually forecast before reading it" — the historically-recorded
    spreadsheet forecast IS the answer there.

    This module asks a different question: "where is the engine, as it runs TODAY,
    biased for this reader". The workbook backfill cannot answer it. Those rows
    predate the engine and are coarse — on the reference library all four Literary
    Fiction books carry the identical pred_wa 6.0245, a single spreadsheet-era
    genre guess. Measured on that library, including them flattens the signal to a
    0.49 WA spread and reports Literary Fiction at -0.03; excluding them gives a
    1.72 spread and -1.16, which is the real and actionable number.

    So: drop the backfill rows, then run the SAME `visible_rows` filter over what
    is left (never a re-prediction audit row, never an unread book, one row per
    book) — which now resolves live > retro_sweep. Both remaining kinds are
    engine-produced by a uniform method: a live row is what the engine said at read
    time, a retro_sweep row is today's engine leave-one-out. Req-1 filtering is not
    duplicated here; it stays owned by `delta_log_view`.

    A reader with no backfill rows (i.e. everyone except the seed tenant) gets
    exactly the same rows they would have got anyway.
    """
    if backfill_marker is None:
        rows = list(entries or ())
    else:
        rows = [e for e in (entries or ())
                if e.get("logged_at") != backfill_marker]
    return delta_log_view.visible_rows(rows, finished_titles, backfill_marker)


def _surprise_by_genre(delta_rows, book_meta):
    """Signed predicted-vs-actual bias per genre, from already-deduped rows.

    Sign follows the repo's delta convention: d = actual - predicted, so POSITIVE
    means the engine under-predicted the reader's enjoyment. Rows are expected to
    have come through `engine_forecast_rows` above — so this can never be inflated
    by the `baseline_repredict:` rows that re-prediction writes for unread peers,
    nor flattened by the pre-engine workbook backfill.

    A genre's surprise `n` can differ from its `n_books` by one or two, and that is
    correct rather than a bug to reconcile: this buckets by `pred_genre` — the genre
    the book was PREDICTED as — while `n_books` counts the genre the book carries
    NOW. A book re-genred after it was predicted therefore lands in one bucket for
    affinity and another for surprise. Keying surprise on the prediction-time genre
    is the honest choice, because the error being measured is the error the engine
    made while treating the book as that genre. (Only a row with no `pred_genre` at
    all falls back to the book's current genre, via `book_meta`.)"""
    if not delta_rows:
        return {}
    meta = book_meta or {}
    buckets = {}
    for r in delta_rows:
        pred, act = r.get("pred_wa"), r.get("act_wa")
        if pred is None or act is None:
            continue
        genre = (r.get("pred_genre") or "").strip()
        if not genre:
            key = str(r.get("title") or "").strip().lower()
            genre = str((meta.get(key) or {}).get("genre") or "").strip()
        if not genre:
            continue
        try:
            buckets.setdefault(genre, []).append(float(act) - float(pred))
        except (TypeError, ValueError):
            continue
    out = {}
    for genre, ds in buckets.items():
        if len(ds) < MIN_SURPRISE_N:
            continue
        out[genre] = {
            "n": len(ds),
            "mean_signed": _round(_mean(ds)),
            "mae": _round(_mean([abs(d) for d in ds])),
            "direction": ("under-predicted" if _mean(ds) > 0.05
                          else "over-predicted" if _mean(ds) < -0.05
                          else "on-target"),
        }
    return out


def _year_shares(rows):
    """Per-genre share of each reading year — the drift signal."""
    per_year = {}
    for r in rows:
        if r["year"] is None:
            continue
        per_year.setdefault(r["year"], []).append(r["genre"])
    out = {}
    for year, genres in per_year.items():
        total = len(genres)
        for genre in set(genres):
            out.setdefault(genre, {})[str(year)] = _round(
                genres.count(genre) / total, 3)
    return out


# ---------------------------------------------------------------------------
# The brief — the deterministic text the LLM reads
# ---------------------------------------------------------------------------
def format_brief(evidence, max_genres=20):
    """Render `genre_evidence` as compact text for the prompt.

    Kept as a separate function on purpose: it is what makes the LLM half
    auditable. Whatever the model says, this is exactly what it was told, and a
    test can assert the numbers in it match the evidence dict."""
    lib = evidence.get("library", {})
    genres = evidence.get("genres", [])[:max_genres]
    out = [
        f"LIBRARY: {lib.get('n_books', 0)} rated books across "
        f"{lib.get('n_genres_read', 0)} genres. Mean WA {lib.get('mean_wa')}.",
        "",
        "GENRE EVIDENCE (affinity = shrunk mean WA with an 80% band; "
        "surprise = actual minus predicted, so + means the engine UNDER-rates "
        "this genre for them):",
    ]
    for g in genres:
        if g["status"] == "unread":
            out.append(
                f"- {g['genre']}: NO rated books. "
                f"{g['tbr_open']} on their to-read list. No affinity number exists.")
            continue
        s = g.get("surprise")
        stxt = (f" surprise {s['mean_signed']:+.2f} over {s['n']} finished "
                f"({s['direction']})" if s else " surprise n/a")
        out.append(
            f"- {g['genre']}: affinity {g['affinity']} "
            f"[{g['band_low']}-{g['band_high']}], {g['n_books']} books "
            f"({g['read_share']:.0%} of library), raw mean {g['raw_mean_wa']}, "
            f"best {g['best_wa']} / worst {g['worst_wa']}, evidence "
            f"{g['evidence']},{stxt}, {g['tbr_open']} on to-read.")
        prof = {k: v for k, v in (g.get("profile") or {}).items() if v is not None}
        if prof:
            hi = sorted(prof.items(), key=lambda kv: -kv[1])[:3]
            lo = sorted(prof.items(), key=lambda kv: kv[1])[:2]
            out.append("    strongest vs library: " +
                       ", ".join(f"{k} {v:+.1f}sd" for k, v in hi) +
                       " | weakest: " +
                       ", ".join(f"{k} {v:+.1f}sd" for k, v in lo))
    drivers = lib.get("drivers") or []
    if drivers:
        out += ["",
                "WHAT SEPARATES THEIR BEST BOOKS FROM THEIR WORST "
                "(top quartile minus bottom quartile, per component):"]
        for d in drivers[:6]:
            out.append(f"- {d['component']}: {d['top_quartile']} vs "
                       f"{d['bottom_quartile']} (gap {d['gap']})")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The LLM half — narration over the numbers above, and nothing more
# ---------------------------------------------------------------------------
_NUMBER_CLAIM = re.compile(r"\d+\.\d+")


def _scrub_types(types):
    """Drop any 'type' hypothesis that states a decimal number.

    The types half has NO data behind it by construction — it names a mode or
    tradition the 16-genre schema has no label for. A decimal in that text is
    therefore an invented WA, band or rank, which is precisely the class of claim
    this codebase refuses everywhere else (the omitted conformal interval, the
    forbidden WA/rank in a saved blurb). Cheap, mechanical, and it fails closed."""
    kept = []
    for t in types:
        blob = " ".join(str(t.get(k, "") or "") for k in ("label", "hypothesis",
                                                          "drawn_from"))
        if _NUMBER_CLAIM.search(blob):
            continue
        kept.append(t)
    return kept


def recommend_genres(evidence, client, model=GENRE_MODEL, focus=None,
                     n_genres=3, n_types=2):
    """ONE LLM call: argue for a few genres/types from `evidence`. Returns a dict.

    `focus` is an optional free-text steer from the reader ("something shorter",
    "I want to branch out"). Everything the model may assert numerically is in the
    brief; it is told to copy those numbers and forbidden to invent others.

    Returns {"genres": [...], "types": [...], "caution": str, "brief": str}. Each
    genre entry carries a `discover_request` — a ready-to-run Discover string, so
    the reader goes from "read more Gothic" to actual scored candidates through
    the EXISTING pipeline. Nothing here scores, ranks or writes.
    """
    brief = format_brief(evidence)
    allowed = [g["genre"] for g in evidence.get("genres", [])]
    focus_line = (f"\nThe reader also said: {focus.strip()}\n" if focus and
                  focus.strip() else "")
    prompt = f'''You are advising a reader whose taste is measured in detail. Below is evidence computed from THEIR OWN ratings — not your opinion of these genres. Recommend what they should read MORE of.

{brief}
{focus_line}
Their genre schema (copy these spellings EXACTLY — never invent or alter one):
{", ".join(allowed)}

Give TWO kinds of recommendation, and keep them strictly separate:

A) "genres" — up to {n_genres} picks, each ONE genre copied exactly from the schema list above. For each, make the case IN TERMS OF THE NUMBERS ABOVE. Rules:
   - Quote only numbers that appear in the evidence. NEVER invent, estimate or extrapolate a number.
   - A wide band or thin evidence must be stated, not hidden: "high but on only 3 books" is the honest phrasing.
   - Volume is not affinity. Do NOT recommend their most-read genre merely because it is biggest; if the case for it is "they already read a lot of it", say that instead of dressing it up.
   - A genre the engine UNDER-predicts (positive surprise) is a strong pick — they enjoy it more than the model expects. Prefer these when the evidence supports it.
   - A genre with no rated books can be recommended, but you must say plainly that there is no affinity number for it and rest the case on the component profile or their drivers.

B) "types" — up to {n_types} finer kinds of book that their 16-genre schema has NO label for: a mode, tradition, structure or register (e.g. "secondary-world fantasy that sticks its ending", "19th-century psychological realism"). Rules:
   - These are HYPOTHESES drawn from the component evidence, not measurements.
   - State NO numbers of any kind in a type. No scores, no bands, no ranks, no decimals.
   - Say which part of the evidence suggested it in "drawn_from".

For every entry give "discover_request": a single natural-sentence book-search request that would find such books, phrased the way the reader would type it (e.g. "gothic novels with strong endings and a bleak atmosphere"). It must NOT name a specific title.

Respond with ONLY a JSON object — no prose, no markdown:
{{"genres": [{{"genre": "...", "case": "...", "evidence_cited": "...", "confidence": "high|medium|low", "discover_request": "..."}}],
 "types": [{{"label": "...", "hypothesis": "...", "drawn_from": "...", "discover_request": "..."}}],
 "caution": "one sentence on the biggest weakness in this evidence"}}'''

    msg = client.messages.create(model=model, max_tokens=1800,
                                 messages=[{"role": "user", "content": prompt}])
    data = rl._extract_json(msg.content[0].text.strip())

    allowed_set = set(allowed)
    genres = []
    seen = set()
    for g in data.get("genres", []) or []:
        name = str(g.get("genre", "") or "").strip()
        # Backstop, same rule as Discover's candidate filter: a genre outside the
        # schema has no weights row, and every WA roll-up in this codebase reads
        # weights defensively — so it would score 0.00 and look like an answer.
        # See test_genre_guard.py.
        if name not in allowed_set or name in seen:
            continue
        seen.add(name)
        ev = next((e for e in evidence.get("genres", []) if e["genre"] == name), {})
        conf = str(g.get("confidence", "") or "").strip().lower()
        genres.append({
            "genre": name,
            "case": str(g.get("case", "") or "").strip(),
            "evidence_cited": str(g.get("evidence_cited", "") or "").strip(),
            "confidence": conf if conf in ("high", "medium", "low") else "medium",
            "discover_request": str(g.get("discover_request", "") or "").strip(),
            # The numbers travel WITH the recommendation, straight from the
            # evidence dict — so the UI renders what was computed, never what the
            # model retyped into its prose.
            "affinity": ev.get("affinity"),
            "band_low": ev.get("band_low"),
            "band_high": ev.get("band_high"),
            "n_books": ev.get("n_books"),
            "evidence_tier": ev.get("evidence"),
            "surprise": ev.get("surprise"),
            "tbr_open": ev.get("tbr_open"),
            "status": ev.get("status", "read"),
        })

    types = _scrub_types([
        {"label": str(t.get("label", "") or "").strip(),
         "hypothesis": str(t.get("hypothesis", "") or "").strip(),
         "drawn_from": str(t.get("drawn_from", "") or "").strip(),
         "discover_request": str(t.get("discover_request", "") or "").strip()}
        for t in (data.get("types") or []) if str(t.get("label", "") or "").strip()
    ])

    return {
        "genres": genres[:n_genres],
        "types": types[:n_types],
        "caution": str(data.get("caution", "") or "").strip(),
        "brief": brief,
    }


# ---------------------------------------------------------------------------
# CLI — inspect the evidence without spending an API call
# ---------------------------------------------------------------------------
def _main():
    import argparse

    import db_loader
    import db_backend
    import db_write

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--brief", action="store_true",
                    help="print the LLM brief instead of the raw table")
    args = ap.parse_args()

    books, gw, _gcw = db_loader.load_from_db()
    con = db_backend.connect(db_write.DB, readonly=True)
    user_id = db_backend.DEFAULT_USER_ID
    cols = ["id", "title", "pred_wa", "act_wa", "pred_genre", "tag", "logged_at",
            "user_id"]
    entries = [dict(zip(cols, r)) for r in con.execute(
        "SELECT id, title, pred_wa, act_wa, pred_genre, tag, logged_at, user_id "
        "FROM delta_log WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]
    finished, meta = set(), {}
    for t, g in con.execute(
            "SELECT title, genre FROM books WHERE user_id=? AND status=?",
            (user_id, "finished")).fetchall():
        key = (t or "").strip().lower()
        finished.add(key)
        meta[key] = {"genre": g}
    tbr = dict(con.execute(
        "SELECT genre, COUNT(*) FROM recommendations WHERE user_id=? AND done=0 "
        "GROUP BY genre", (user_id,)).fetchall())
    allowed = [r[0] for r in con.execute("SELECT genre FROM genre_weights")]
    con.close()

    rows = engine_forecast_rows(entries, finished,
                                db_write.DELTA_BACKFILL_MARKER)
    ev = genre_evidence(books, delta_rows=rows, book_meta=meta,
                        genre_weights=gw, tbr_counts=tbr, allowed_genres=allowed)
    if args.brief:
        print(format_brief(ev))
        return
    lib = ev["library"]
    print(f"{lib['n_books']} books · mean WA {lib['mean_wa']} · "
          f"shrinkage k={lib['shrinkage_k_books']} books\n")
    hdr = f"{'genre':<30}{'n':>4}{'raw':>7}{'affin':>8}{'80% band':>16}{'surprise':>11}{'tbr':>6}"
    print(hdr)
    print("-" * len(hdr))
    for g in ev["genres"]:
        if g["status"] == "unread":
            print(f"{g['genre']:<30}{0:>4}{'—':>7}{'—':>8}{'—':>16}{'—':>11}"
                  f"{g['tbr_open']:>6}")
            continue
        s = g["surprise"]
        band = f"{g['band_low']:.2f}-{g['band_high']:.2f}"
        surp = f"{s['mean_signed']:+.2f}" if s else "—"
        print(f"{g['genre']:<30}{g['n_books']:>4}{g['raw_mean_wa']:>7.2f}"
              f"{g['affinity']:>8.2f}{band:>16}{surp:>11}{g['tbr_open']:>6}")


if __name__ == "__main__":
    _main()
