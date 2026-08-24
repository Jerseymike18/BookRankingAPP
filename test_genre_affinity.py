"""
test_genre_affinity.py — the genre-recommendation evidence layer and its guards.

WHY THIS EXISTS
---------------
`genre_affinity.py` is the first thing in this codebase that tells the reader what
to read at the GENRE level rather than the book level. Everything it says is a
claim about their taste, so the ways it can be quietly wrong are the ways that
matter:

  * **Ranking on raw means.** On the reference library the naive ranking puts
    Russian Literature (3 books) and Gothic Fiction (2) above Epic Fantasy (59).
    The shrinkage exists to stop a 2-book genre out-ranking a 59-book one on
    noise, and the 80% band exists so thin evidence LOOKS thin. Both are checked.

  * **The worldbuilding mask.** Worldbuilding is scored 0 for realist genres, so
    an unmasked z-profile reports Literary Fiction at -2.26 on Depth2 — "this
    reader hates its worldbuilding" when it means "there is none to score". A
    regression here would quietly libel every realist genre.

  * **The surprise set.** Signed surprise must come from ENGINE-produced forecasts
    only. Including the pre-engine workbook backfill (all four Literary Fiction
    books share one spreadsheet-era pred_wa) flattens the measured spread from
    1.72 WA to 0.49 and flips Literary Fiction from -1.16 to -0.03. It must also
    never see a `baseline_repredict:` row, whose "actual" is another prediction.

  * **The LLM's freedom.** The model narrates; it must not invent. A recommended
    genre outside the schema would have no `genre_weights` row and would score a
    confident-looking WA of 0.00 downstream (see test_genre_guard.py). And a
    "type" — which by construction has NO data behind it — stating a decimal is
    an invented score, the same class of claim the omitted conformal interval and
    the blurb rank-leak fix both refuse.

Zero-API and zero-database: the library is a hand-built frame and the LLM client
is a stub, so this runs offline and spends nothing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import genre_affinity as ga

FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


# ---------------------------------------------------------------------------
# Fixtures — a miniature library with the shapes that matter.
# ---------------------------------------------------------------------------
_COMP_DEFAULT = {c: 7.0 for c in ga.COMPONENTS}


def _book(title, genre, wa, year=2025, **comps):
    row = {"Book": title, "Genre": genre, "WA": wa, "Year": year}
    row.update(_COMP_DEFAULT)
    row.update(comps)
    return row


def _library():
    """20 Epic Fantasy at ~8.0, 2 Gothic at ~9.0, 4 Literary Fiction at ~6.0.

    Gothic is the trap: its RAW mean is the highest in the library on two books.
    Literary Fiction is realist — its three worldbuilding components are 0, as the
    real schema stores them.

    Components carry real spread (they track WA, plus a per-book wobble), because
    a constant column has no standard deviation and the z-profile correctly
    declines to score it — a flat fixture would pass the mask checks vacuously."""
    rows = []
    for i in range(20):
        wa = 8.0 + (0.1 if i % 2 else -0.1)
        rows.append(_book(f"EF{i}", "Epic Fantasy", wa,
                          **{c: wa + 0.1 * ((i + j) % 5) for j, c in enumerate(ga.COMPONENTS)}))
    for i in range(2):
        rows.append(_book(f"G{i}", "Gothic Fiction", 9.0,
                          **{c: 9.0 + 0.1 * i for c in ga.COMPONENTS}, ))
    for i in range(4):
        rows.append(_book(f"LF{i}", "Literary Fiction", 6.0,
                          **{c: 6.0 + 0.1 * i for c in ga.COMPONENTS}))
    # Realist: worldbuilding is not scored at all, exactly as the schema stores it.
    for r in rows:
        if r["Genre"] in ("Gothic Fiction", "Literary Fiction"):
            for c in ("Depth2", "Integration", "Originality"):
                r[c] = 0.0
    # Gothic's distinguishing trait: prose well above the library.
    for r in rows:
        if r["Genre"] == "Gothic Fiction":
            r["Prose"] = 9.8
    return pd.DataFrame(rows)


_WEIGHTS = {
    "Epic Fantasy": {"Worldbuilding": 0.1},
    "Gothic Fiction": {"Worldbuilding": 0.0},
    "Literary Fiction": {"Worldbuilding": 0.0},
}


class _StubClient:
    """Returns a canned JSON body; records the prompt it was given."""

    def __init__(self, body):
        self._body = body
        self.prompt = None
        self.messages = self

    def create(self, **kw):
        self.prompt = kw["messages"][0]["content"]

        class _Block:
            text = self._body
        return type("R", (), {"content": [_Block()]})()


def run():
    print("\n" + "=" * 60)
    print("  GENRE AFFINITY — evidence layer + LLM guards")
    print("=" * 60 + "\n")

    books = _library()

    # ── 1. Shrinkage: volume beats a thin high mean ────────────────────────
    ev = ga.genre_evidence(books, genre_weights=_WEIGHTS)
    by = {g["genre"]: g for g in ev["genres"]}
    gothic, ef = by["Gothic Fiction"], by["Epic Fantasy"]
    check("raw mean would rank the 2-book genre top (the trap this guards)",
          gothic["raw_mean_wa"] > ef["raw_mean_wa"],
          f"gothic raw {gothic['raw_mean_wa']} > EF raw {ef['raw_mean_wa']}")
    check("shrinkage pulls the 2-book genre BELOW its own raw mean",
          gothic["affinity"] < gothic["raw_mean_wa"],
          f"{gothic['affinity']} < {gothic['raw_mean_wa']}")
    check("the 20-book genre is barely shrunk",
          abs(ef["affinity"] - ef["raw_mean_wa"]) < 0.05,
          f"affinity {ef['affinity']} vs raw {ef['raw_mean_wa']}")
    check("the thin genre's band is WIDER than the well-evidenced one's",
          gothic["band_width"] > ef["band_width"],
          f"{gothic['band_width']} vs {ef['band_width']}")
    check("evidence tier labels n",
          ef["evidence"] == "strong" and gothic["evidence"] == "thin",
          f"EF={ef['evidence']} gothic={gothic['evidence']}")
    check("band brackets the affinity estimate",
          all(g["band_low"] <= g["affinity"] <= g["band_high"]
              for g in ev["genres"] if g["affinity"] is not None))
    check("read share is reported separately from affinity",
          abs(ef["read_share"] - 20 / 26) < 5e-5, f"{ef['read_share']}")

    # A genre whose mean sits AT the library mean must not move.
    check("shrinkage is toward the library mean (a genre at it does not move)",
          abs(by["Epic Fantasy"]["affinity"] - 8.0) < 0.2,
          f"library mean {ev['library']['mean_wa']}, EF {ef['affinity']}")

    # ── 2. The worldbuilding mask ──────────────────────────────────────────
    lf = by["Literary Fiction"]
    check("realist genre's worldbuilding components are MASKED, not negative",
          all(lf["profile"][c] is None for c in ("Depth2", "Integration", "Originality")),
          f"{ {c: lf['profile'][c] for c in ('Depth2', 'Integration', 'Originality')} }")
    check("a worldbuilding genre keeps its worldbuilding profile",
          ef["profile"]["Depth2"] is not None)
    check("non-worldbuilding components still profile on a realist genre",
          lf["profile"]["Plot"] is not None)
    # Without weights the mask must fall back to the data (all-zero => realist).
    ev_nw = ga.genre_evidence(books)
    lf_nw = next(g for g in ev_nw["genres"] if g["genre"] == "Literary Fiction")
    check("mask falls back to the data when no weights are supplied",
          lf_nw["profile"]["Depth2"] is None)
    check("Gothic's high Prose surfaces as a positive z",
          by["Gothic Fiction"]["profile"]["Prose"] > 0,
          f"{by['Gothic Fiction']['profile']['Prose']}")

    # ── 3. Unread schema genres: a door, not a number ──────────────────────
    ev_u = ga.genre_evidence(books, genre_weights=_WEIGHTS,
                             allowed_genres=["Epic Fantasy", "Urban Fantasy"],
                             tbr_counts={"Urban Fantasy": 17})
    uf = next(g for g in ev_u["genres"] if g["genre"] == "Urban Fantasy")
    check("an unread schema genre appears at all", uf is not None)
    check("...with NO invented affinity number",
          uf["affinity"] is None and uf["band_low"] is None
          and uf["raw_mean_wa"] is None, f"{uf}")
    check("...but carries its real to-read count", uf["tbr_open"] == 17)
    check("unread genres sort after every read genre",
          [g["status"] for g in ev_u["genres"]][-1] == "unread")

    # ── 4. Surprise: the right rows, and only the right rows ───────────────
    MARKER = "2026-06-27T00:00:00Z"
    finished = {"lf0", "lf1", "lf2", "lf3", "ef0"}
    entries = [
        # engine-produced (retro LOO): Literary Fiction badly over-predicted.
        {"id": 10, "title": "LF0", "pred_wa": 7.2, "act_wa": 6.0,
         "pred_genre": "Literary Fiction", "tag": "retro_sweep_v1_shrunk",
         "logged_at": "2026-07-01T00:00:00Z"},
        {"id": 11, "title": "LF1", "pred_wa": 7.1, "act_wa": 6.0,
         "pred_genre": "Literary Fiction", "tag": "retro_sweep_v1_shrunk",
         "logged_at": "2026-07-01T00:00:00Z"},
        # pre-engine workbook backfill for the SAME books — coarse, identical pred.
        {"id": 20, "title": "LF0", "pred_wa": 6.02, "act_wa": 6.0,
         "pred_genre": "Literary Fiction", "tag": None, "logged_at": MARKER},
        {"id": 21, "title": "LF1", "pred_wa": 6.02, "act_wa": 6.0,
         "pred_genre": "Literary Fiction", "tag": None, "logged_at": MARKER},
        # a re-prediction audit row — its "actual" is another prediction.
        {"id": 30, "title": "EF0", "pred_wa": 8.0, "act_wa": 9.9,
         "pred_genre": "Epic Fantasy", "tag": "baseline_repredict:Something",
         "logged_at": "2026-07-02T00:00:00Z"},
        # an unread book — must never count.
        {"id": 40, "title": "NotRead", "pred_wa": 5.0, "act_wa": 9.0,
         "pred_genre": "Epic Fantasy", "tag": None,
         "logged_at": "2026-07-02T00:00:00Z"},
    ]
    rows = ga.engine_forecast_rows(entries, finished, MARKER)
    ids = sorted(r["id"] for r in rows)
    check("workbook-backfill rows are dropped from the surprise set",
          20 not in ids and 21 not in ids, f"ids={ids}")
    check("...and the engine-produced rows for those books survive",
          ids == [10, 11], f"ids={ids}")
    check("a baseline_repredict row never enters the surprise set", 30 not in ids)
    check("an unread book never enters the surprise set", 40 not in ids)

    ev_s = ga.genre_evidence(books, delta_rows=rows, genre_weights=_WEIGHTS)
    lf_s = next(g for g in ev_s["genres"] if g["genre"] == "Literary Fiction")
    check("signed surprise follows d = actual - predicted",
          lf_s["surprise"]["mean_signed"] < 0,
          f"{lf_s['surprise']}")
    check("...and is labelled by direction",
          lf_s["surprise"]["direction"] == "over-predicted")
    check("a genre with too few finished predictions reports NO surprise",
          next(g for g in ev_s["genres"]
               if g["genre"] == "Gothic Fiction")["surprise"] is None)
    check("no delta rows at all -> surprise absent, never zero",
          all(g["surprise"] is None for g in ev["genres"]))

    # Including the backfill would flatten the signal — pin the direction of the
    # difference so a future "simplification" back to visible_rows is visible.
    import delta_log_view
    naive = delta_log_view.visible_rows(entries, finished, MARKER)
    naive_ev = ga.genre_evidence(books, delta_rows=naive, genre_weights=_WEIGHTS)
    naive_lf = next(g for g in naive_ev["genres"]
                    if g["genre"] == "Literary Fiction")["surprise"]
    check("the backfill really does flatten it (why the selector exists)",
          abs(naive_lf["mean_signed"]) < abs(lf_s["surprise"]["mean_signed"]),
          f"backfill {naive_lf['mean_signed']} vs engine {lf_s['surprise']['mean_signed']}")

    # ── 5. Fallbacks + the brief ───────────────────────────────────────────
    rows_nogenre = [{"id": 1, "title": "LF0", "pred_wa": 7.2, "act_wa": 6.0,
                     "pred_genre": None, "tag": None, "logged_at": "x"},
                    {"id": 2, "title": "LF1", "pred_wa": 7.2, "act_wa": 6.0,
                     "pred_genre": "", "tag": None, "logged_at": "x"}]
    ev_f = ga.genre_evidence(books, delta_rows=rows_nogenre,
                             book_meta={"lf0": {"genre": "Literary Fiction"},
                                        "lf1": {"genre": "Literary Fiction"}},
                             genre_weights=_WEIGHTS)
    check("a delta row with no pred_genre falls back to book_meta",
          next(g for g in ev_f["genres"]
               if g["genre"] == "Literary Fiction")["surprise"]["n"] == 2)

    check("an empty library returns empty, not an exception",
          ga.genre_evidence(pd.DataFrame([]))["genres"] == [])

    brief = ga.format_brief(ev_s)
    check("the brief states the affinity number verbatim",
          str(ef["affinity"]) in brief or f"{ef['affinity']:.2f}" in brief[:2000])
    check("the brief marks an unread genre as having no number",
          "No affinity number exists" in ga.format_brief(ev_u))
    check("the brief carries the drivers block",
          "SEPARATES THEIR BEST BOOKS" in brief)

    # ── 6. The LLM half: narration only ────────────────────────────────────
    body = """{"genres": [
      {"genre": "Gothic Fiction", "case": "thin but strong", "evidence_cited": "2 books",
       "confidence": "low", "discover_request": "atmospheric gothic novels"},
      {"genre": "Steampunk", "case": "invented genre", "evidence_cited": "none",
       "confidence": "high", "discover_request": "steampunk"},
      {"genre": "Gothic Fiction", "case": "duplicate", "evidence_cited": "x",
       "confidence": "high", "discover_request": "y"}],
     "types": [
      {"label": "ending-forward secondary world", "hypothesis": "they reward a landed ending",
       "drawn_from": "Ending gap", "discover_request": "fantasy with a strong ending"},
      {"label": "bad type", "hypothesis": "these would score about 8.4 for you",
       "drawn_from": "made up", "discover_request": "z"}],
     "caution": "thin evidence"}"""
    client = _StubClient(body)
    out = ga.recommend_genres(ev, client, model="stub")
    names = [g["genre"] for g in out["genres"]]
    check("a genre outside the schema is REFUSED (would score WA 0.00)",
          "Steampunk" not in names, f"{names}")
    check("a duplicate genre pick is collapsed", names.count("Gothic Fiction") == 1)
    check("a type stating a decimal score is scrubbed",
          [t["label"] for t in out["types"]] == ["ending-forward secondary world"],
          f"{[t['label'] for t in out['types']]}")
    check("the surviving type carries no numeric claim",
          not any(ch.isdigit() for t in out["types"] for ch in t["hypothesis"]))
    g0 = out["genres"][0]
    check("numbers travel from the EVIDENCE, not the model's prose",
          g0["affinity"] == by["Gothic Fiction"]["affinity"]
          and g0["n_books"] == 2, f"{g0['affinity']} / {g0['n_books']}")
    check("each pick carries a ready-to-run Discover request",
          bool(g0["discover_request"]))
    check("an out-of-range confidence falls back to medium",
          ga.recommend_genres(
              ev, _StubClient('{"genres": [{"genre": "Gothic Fiction", '
                              '"confidence": "certain"}], "types": []}'),
              model="stub")["genres"][0]["confidence"] == "medium")
    check("the prompt forbids inventing numbers",
          "NEVER invent" in client.prompt)
    check("the prompt refuses to let volume masquerade as affinity",
          "Volume is not affinity" in client.prompt)
    check("the brief the model saw is returned for audit",
          out["brief"] == ga.format_brief(ev))
    # A malformed body RAISES rather than returning an empty recommendation.
    # There is no safe default here: an empty result renders as "nothing to
    # suggest", which is a claim about the reader's library rather than about the
    # call having failed. The endpoint turns this into a visible error instead.
    try:
        ga.recommend_genres(ev, _StubClient("not json"), model="stub")
        raised = False
    except ValueError:
        raised = True
    check("a malformed LLM body RAISES (never a silent empty recommendation)",
          raised)

    # ── 7. The endpoint: gated, scoped, and it spends nothing on the evidence ──
    import inspect
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
    import importlib
    main = importlib.import_module("backend.main")
    src = inspect.getsource(main.discover_genres)

    # Read the real dependency off the signature rather than grepping the source:
    # a grep passes as happily on a comment as on the wiring.
    _dep = inspect.signature(main.discover_genres).parameters["user_id"].default
    check("endpoint is auth-gated like every other data route",
          getattr(_dep, "dependency", None) is main.auth.get_current_user_id,
          f"dependency={getattr(_dep, 'dependency', _dep)}")
    check("endpoint is rate-limited on the bucket that gates MONEY",
          '_rate_limit(request, "llm"' in src)
    check("every read is tenant-scoped",
          src.count("WHERE user_id=?") + src.count("user_id=? AND") >= 3
          and "user_id" in src)
    check("endpoint uses the engine-produced surprise set, not the raw log",
          "engine_forecast_rows" in src)
    check("endpoint writes nothing",
          not any(w in src for w in ("db_write.add_", "db_write.update_",
                                     "db_write.set_", "INSERT", "UPDATE ", "DELETE")))
    check("endpoint opens the connection read-only",
          "readonly=True" in src)
    check("a too-small library is refused rather than argued from",
          "MIN_LIBRARY_BOOKS" in src and "status_code=404" in src)

    # Live call against the real local library, with the LLM stubbed — proves the
    # wiring end to end without spending anything.
    from fastapi.testclient import TestClient
    canned = ('{"genres": [{"genre": "%s", "case": "c", "evidence_cited": "e", '
              '"confidence": "high", "discover_request": "some books"}], '
              '"types": [], "caution": "thin"}')
    con_books = main._get_engine(main.db_backend.DEFAULT_USER_ID)[0]
    a_genre = str(con_books["Genre"].iloc[0])
    real_get_client = main._rp.get_client
    stub = _StubClient(canned % a_genre)
    main._rp.get_client = lambda *a, **k: stub
    try:
        client = TestClient(main.app)
        r = client.post("/api/discover/genres", json={"focus": "branch out"})
        check("endpoint returns 200 on the real library", r.status_code == 200,
              f"status={r.status_code}")
        body = r.json()
        check("...with the picks, the evidence, and the library block",
              all(k in body for k in ("genres", "types", "evidence", "library",
                                      "caution", "provenance")),
              f"keys={sorted(body)}")
        check("...and the pick's numbers match the evidence row exactly",
              body["genres"] and body["genres"][0]["affinity"] == next(
                  e["affinity"] for e in body["evidence"]
                  if e["genre"] == body["genres"][0]["genre"]))
        check("the reader's focus text reaches the prompt",
              "branch out" in (stub.prompt or ""))
        check("the prompt carries the reader's OWN library size",
              f"{body['library']['n_books']} rated books" in (stub.prompt or ""))
    finally:
        main._rp.get_client = real_get_client

    print("\n" + "=" * 60)
    if FAILED:
        print(f"  {len(FAILED)} CHECK(S) FAILED: {', '.join(FAILED)}")
    else:
        print("  ALL 46 CHECKS PASSED — genre affinity is healthy.")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(run())
