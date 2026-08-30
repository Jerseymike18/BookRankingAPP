"""
import_enrich.py
================
Cheap background classifier for staged Goodreads-import rows. Fills the two
routing-critical fields the export doesn't carry — `kind` (fiction / nonfiction)
and a best-guess `genre` drawn from the USER's own taxonomy — so the review
table lands mostly pre-filled and the user just confirms.

It uses DISCOVER_MODEL (cheap Sonnet 4.6 — the same throughput tier Discover
uses), NEVER the grounded Opus research path, and touches NO prediction math: it
classifies metadata only. Every write goes through db_write.set_staging_enrichment.

Best-effort throughout, by design:
  * no API key / client build fails  -> rows marked enrich_state='error' (the UI
    stops waiting and prompts manual classification in review; never stuck on
    'pending' forever).
  * a per-book model hiccup / unusable reply -> that row -> 'error'.
  * a confident kind but no taxonomy-matching genre -> kind is written, genre is
    left for the user to pick.
Bounded by IMPORT_ENRICH_CONCURRENCY (default 8) and a per-run IMPORT_ENRICH_MAX
cap (default 500) whose overflow is REPORTED (deferred), never silently dropped.
"""

import os


def _canonical_genre(raw, pool):
    """Map a model-returned genre to the exact taxonomy spelling (case-insensitive),
    or None if it isn't one of the user's genres — the user picks it in review."""
    if not raw:
        return None
    r = str(raw).strip().lower()
    for g in pool:
        if str(g).strip().lower() == r:
            return g
    return None


def _classify_prompt(title, author, fiction_genres, nonfiction_genres):
    return (
        'You are cataloguing a book for a personal reading tracker. Decide whether '
        'it is fiction or nonfiction, pick the single best-fitting genre from the '
        'matching list (choose one, VERBATIM from the list), and give your best '
        'estimate of its total word count.\n\n'
        f'BOOK: "{title}"' + (f' by {author}' if author else '') + '\n\n'
        f'FICTION genres: {", ".join(fiction_genres) or "(none)"}\n'
        f'NONFICTION genres: {", ".join(nonfiction_genres) or "(none)"}\n\n'
        'Respond with ONLY this JSON, no prose or markdown:\n'
        '{"kind": "fiction" | "nonfiction", "genre": "<one genre from the matching '
        'list>", "words": <best integer estimate of the total word count, e.g. '
        '150000, or null if unsure>}')


def classify_book(title, author, fiction_genres, nonfiction_genres, client, model=None):
    """One cheap LLM call -> {"kind": "fiction"|"nonfiction", "genre": <str|None>,
    "words": <int|None>}. Returns {} on any failure or unusable reply. `genre` is
    canonicalized to the exact taxonomy spelling (or None when the pick isn't in
    the user's list); `words` is the model's estimate (or None), which the caller
    prefers over the crude page-count heuristic. The user can still edit it — word
    counts are treated as editable estimates everywhere in the app."""
    import research_predict as rp
    import research_layer as rl
    model = model or rp.DISCOVER_MODEL
    fic = [g for g in (fiction_genres or []) if g]
    non = [g for g in (nonfiction_genres or []) if g]
    try:
        msg = client.messages.create(
            model=model, max_tokens=200,
            messages=[{"role": "user",
                       "content": _classify_prompt(title, author, fic, non)}])
        data = rl._extract_json(msg.content[0].text.strip())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    kind = str(data.get("kind", "")).strip().lower()
    if kind not in ("fiction", "nonfiction"):
        return {}
    pool = fic if kind == "fiction" else non
    return {"kind": kind, "genre": _canonical_genre(data.get("genre"), pool),
            "words": rp._coerce_words(data.get("words"))}


def max_per_run():
    """Rows one enrichment run will classify. Rows past this stay `pending` and are
    reported as `deferred` — they are NOT lost, but nothing re-runs on its own, so
    whoever schedules a run owns telling the reader and offering to run again."""
    return int(os.environ.get("IMPORT_ENRICH_MAX", "500"))


def enrich_pending(user_id, batch_id=None, key_path="apikey.txt", concurrency=None,
                   cap=None, client=None, fiction_genres=None, nonfiction_genres=None):
    """Classify this tenant's `enrich_state='pending'` staging rows (kind + genre),
    writing each result through db_write. Bounded-concurrent + best-effort. Returns
    {pending, classified, errors, skipped_no_key, deferred}."""
    import db_write
    rows = [r for r in db_write.get_staging_rows(user_id, batch_id=batch_id, limit=10 ** 9)
            if r.get("enrich_state") == "pending"]
    result = {"pending": len(rows), "classified": 0, "errors": 0,
              "skipped_no_key": 0, "deferred": 0}
    if not rows:
        return result

    cap = cap if cap is not None else max_per_run()
    if len(rows) > cap:
        result["deferred"] = len(rows) - cap
        rows = rows[:cap]

    if client is None:
        try:
            import research_predict as rp
            client = rp.get_client(key_path)
        except Exception:
            # No usable key/client — mark rows 'error' so the UI stops waiting and
            # prompts manual classification (never silently stuck on 'pending').
            for r in rows:
                try:
                    db_write.set_staging_enrichment(user_id, r["id"], enrich_state="error")
                except Exception:
                    pass
            result["skipped_no_key"] = len(rows)
            return result

    fic = fiction_genres if fiction_genres is not None else db_write.list_valid_genres(user_id, "fiction")
    non = nonfiction_genres if nonfiction_genres is not None else db_write.list_valid_genres(user_id, "nonfiction")
    conc = concurrency or int(os.environ.get("IMPORT_ENRICH_CONCURRENCY", "8"))

    def _one(r):
        try:
            res = classify_book(r["title"], r.get("author"), fic, non, client)
            if res.get("kind"):
                db_write.set_staging_enrichment(
                    user_id, r["id"], kind=res["kind"], genre=res.get("genre"),
                    words=res.get("words"), enrich_state="done")
                return "classified"
            db_write.set_staging_enrichment(user_id, r["id"], enrich_state="error")
            return "errors"
        except Exception:
            try:
                db_write.set_staging_enrichment(user_id, r["id"], enrich_state="error")
            except Exception:
                pass
            return "errors"

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, conc)) as ex:
        for outcome in ex.map(_one, rows):
            result[outcome] += 1
    return result
