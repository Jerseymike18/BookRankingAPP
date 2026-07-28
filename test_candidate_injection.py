"""
test_candidate_injection.py — user-typed book is ALWAYS a candidate
==================================================================
Regression for Brief 1: Discover candidate generation used the reader's typed
entry only as an LLM query key, so the exact book they asked about could be
absent from what gets scored/ranked (the model might not surface it, or it might
be filtered as already-read/saved).

`research_predict.generate_candidates` (fiction) and
`nonfiction_research.discover_nonfiction_candidates` (nonfiction) now GUARANTEE
that an explicitly-named single book is injected as a first-class candidate:
  * present even when the model returns different titles;
  * present even when it is already read/saved (the avoidance backstop is a
    deliberate override for an explicit "predict this exact book" request);
  * deduped by the SAME lowercased-title key the pipeline uses — a model
    duplicate collapses into the injected entry, which wins on author.

Mood/theme/genre requests are untouched (fully additive): no book is named, so
nothing is injected and the existing candidate sourcing flows unchanged.

Hermetic: a fake Anthropic client returns canned JSON (classifier + proposer) —
no files, no network, no real LLM. Run: python3 test_candidate_injection.py
(exit 0 = pass, 1 = fail).
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import research_predict as rp
import nonfiction_research as nr


# ── Fake Anthropic client ────────────────────────────────────────────────────
class _Block:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class _Msg:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"


class FakeClient:
    """Routes messages.create by prompt content: the classifier prompt (contains
    'routing a book request') gets `classify_json`; any other prompt (the
    candidate proposer) gets `proposer_json`. Records prompts for inspection."""
    def __init__(self, classify_json, proposer_json):
        self.classify_json = classify_json
        self.proposer_json = proposer_json
        self.messages = self
        self.prompts = []

    def create(self, model=None, max_tokens=None, messages=None, tools=None):
        prompt = messages[0]["content"] if messages else ""
        self.prompts.append(prompt)
        if "routing a book request" in prompt:
            return _Msg(self.classify_json)
        return _Msg(self.proposer_json)


# ── Assertion plumbing ───────────────────────────────────────────────────────
_PASS, _FAIL = [], []


def check(cond, label, detail=""):
    (_PASS if cond else _FAIL).append(label)
    tag = "PASS" if cond else "FAIL"
    line = f"  [{tag}] {label}"
    if detail:
        line += f"  — {detail}"
    print(line)


def _titles(cands):
    return [c["title"].lower() for c in cands]


GENRES = ["Epic Fantasy", "Science Fiction", "Mystery"]


# ── Fiction: generate_candidates ─────────────────────────────────────────────
def test_fiction_typed_title_in_db_appears():
    """User names a book they've ALREADY read → it still appears (avoidance is
    bypassed for an explicit single-book request), and the model didn't surface
    it."""
    read_books = [("Dune", "Frank Herbert"), ("The Hobbit", "J.R.R. Tolkien")]
    fake = FakeClient(
        classify_json='{"category": "single", "title": "Dune", "author": "Frank Herbert"}',
        proposer_json='{"candidates": [{"title": "Some Other Book", "author": "X", "genre": "Science Fiction"}]}',
    )
    res = rp.generate_candidates("predict Dune by Frank Herbert", GENRES,
                                 read_books, tbr_books=[], n=None,
                                 client=fake, model="t")
    titles = _titles(res["candidates"])
    check("dune" in titles,
          "fiction: typed title already in DB still appears (bypasses avoidance)",
          f"titles={titles}")


def test_fiction_typed_title_not_surfaced_appears():
    """User names a book the model does NOT return → injected anyway."""
    fake = FakeClient(
        classify_json='{"category": "single", "title": "Piranesi", "author": "Susanna Clarke"}',
        proposer_json='{"candidates": [{"title": "Unrelated", "author": "Y", "genre": "Mystery"}]}',
    )
    res = rp.generate_candidates("Piranesi by Susanna Clarke", GENRES,
                                 [], tbr_books=[], n=None, client=fake, model="t")
    titles = _titles(res["candidates"])
    check("piranesi" in titles,
          "fiction: typed title the model didn't surface is injected",
          f"titles={titles}")


def test_fiction_dedup_user_entry_wins():
    """Model ALSO returns the typed title (wrong author) → single entry, and the
    user's author wins (deduped by the shared lowercased-title key)."""
    fake = FakeClient(
        classify_json='{"category": "single", "title": "Dune", "author": "Frank Herbert"}',
        proposer_json='{"candidates": [{"title": "Dune", "author": "WRONG PERSON", "genre": "Science Fiction"}]}',
    )
    res = rp.generate_candidates("Dune", GENRES, [], tbr_books=[], n=None,
                                 client=fake, model="t")
    dunes = [c for c in res["candidates"] if c["title"].lower() == "dune"]
    check(len(dunes) == 1, "fiction: model duplicate collapses (one entry)",
          f"count={len(dunes)}")
    check(len(dunes) == 1 and dunes[0]["author"] == "Frank Herbert",
          "fiction: injected user entry wins on author",
          f"author={dunes[0]['author'] if dunes else None}")


def test_fiction_mood_request_untouched():
    """A mood request names no book → nothing injected; existing sourcing flows
    unchanged (purely additive change)."""
    fake = FakeClient(
        classify_json='{"category": "other"}',
        proposer_json='{"candidates": [{"title": "Cozy Book", "author": "Z", "genre": "Mystery"}]}',
    )
    res = rp.generate_candidates("3 cozy mysteries", GENRES, [], tbr_books=[],
                                 n=None, client=fake, model="t")
    titles = [c["title"] for c in res["candidates"]]
    check(titles == ["Cozy Book"],
          "fiction: mood request unchanged (no injection)", f"titles={titles}")


def test_fiction_injected_carries_metadata_and_pinned():
    """Injected book carries author + genre metadata, is flagged requested=True,
    and sits FIRST (pinned) in the returned list; other candidates have no flag."""
    fake = FakeClient(
        classify_json='{"category": "single", "title": "The Way of Kings", "author": "Brandon Sanderson", "genre": "Epic Fantasy"}',
        proposer_json='{"candidates": [{"title": "Other", "author": "Q", "genre": "Mystery"}]}',
    )
    res = rp.generate_candidates("predict The Way of Kings", GENRES, [],
                                 tbr_books=[], n=None, client=fake, model="t")
    first = res["candidates"][0]
    check(first["title"] == "The Way of Kings" and first.get("requested") is True,
          "fiction: requested book is pinned FIRST + flagged requested",
          f"first={first.get('title')!r} requested={first.get('requested')}")
    check(first.get("author") == "Brandon Sanderson" and first.get("genre") == "Epic Fantasy",
          "fiction: injected book carries author + genre metadata",
          f"author={first.get('author')!r} genre={first.get('genre')!r}")
    others = [c for c in res["candidates"] if not c.get("requested")]
    check(all(not c.get("requested") for c in others),
          "fiction: non-requested candidates are not flagged", f"n_others={len(others)}")


def test_fiction_fuzzy_resolves_variant_to_canonical():
    """A variant/partial title fuzzy-resolves to the rated library's canonical
    entry, inheriting its exact title + author + genre."""
    library = [("The Hobbit, or There and Back Again", "J.R.R. Tolkien", "Epic Fantasy"),
               ("Dune", "Frank Herbert", "Science Fiction")]
    fake = FakeClient(
        classify_json='{"category": "single", "title": "the hobbit", "author": "", "genre": ""}',
        proposer_json='{"candidates": []}',
    )
    res = rp.generate_candidates("predict the hobbit", GENRES, [], tbr_books=[],
                                 n=None, client=fake, model="t", library=library)
    first = res["candidates"][0]
    ok = (first["title"] == "The Hobbit, or There and Back Again"
          and first["author"] == "J.R.R. Tolkien"
          and first["genre"] == "Epic Fantasy")
    check(ok, "fiction: fuzzy-resolves a variant title to the canonical library entry",
          f"resolved={first.get('title')!r} / {first.get('author')!r} / {first.get('genre')!r}")


def test_fuzzy_does_not_overmatch():
    """Guard: a shorter title must NOT resolve to a longer different book."""
    library = [("Dune Messiah", "Frank Herbert", "Science Fiction")]
    hit = rp._fuzzy_library_match("Dune", library)
    check(hit is None, "fuzzy: 'Dune' does NOT resolve to 'Dune Messiah'", f"hit={hit}")
    hit2 = rp._fuzzy_library_match("The Hobbit", library)
    check(hit2 is None, "fuzzy: unrelated title returns no match", f"hit={hit2}")


# ── Nonfiction: discover_nonfiction_candidates ───────────────────────────────
def test_nonfiction_typed_title_in_library_appears():
    """User names a nonfiction book already in library/TBR → still appears."""
    fake = FakeClient(
        classify_json='{"category": "single", "title": "Sapiens", "author": "Yuval Noah Harari"}',
        proposer_json='[{"title": "Other NF", "author": "A"}]',
    )
    res = nr.discover_nonfiction_candidates("predict Sapiens", n=8, client=fake,
                                            model="t", avoid_titles={"sapiens"})
    titles = _titles(res)
    check("sapiens" in titles,
          "nonfiction: typed title already in library still appears",
          f"titles={titles}")


def test_nonfiction_typed_title_not_surfaced_appears():
    """User names a nonfiction book the model doesn't return → injected."""
    fake = FakeClient(
        classify_json='{"category": "single", "title": "Chaos", "author": "James Gleick"}',
        proposer_json='[{"title": "Unrelated NF", "author": "B"}]',
    )
    res = nr.discover_nonfiction_candidates("Chaos by James Gleick", client=fake,
                                            model="t", avoid_titles=set())
    titles = _titles(res)
    check("chaos" in titles,
          "nonfiction: typed title the model didn't surface is injected",
          f"titles={titles}")


def test_nonfiction_mood_request_untouched():
    """A nonfiction mood request names no book → nothing injected."""
    fake = FakeClient(
        classify_json='{"category": "other"}',
        proposer_json='[{"title": "NF1", "author": "C"}, {"title": "NF2", "author": "D"}]',
    )
    res = nr.discover_nonfiction_candidates("books about physics", client=fake,
                                            model="t", avoid_titles=set())
    titles = [c["title"] for c in res]
    check(titles == ["NF1", "NF2"],
          "nonfiction: mood request unchanged (no injection)", f"titles={titles}")


def main():
    print("=" * 60)
    print("  candidate injection — user-typed book is always a candidate")
    print("=" * 60)
    test_fiction_typed_title_in_db_appears()
    test_fiction_typed_title_not_surfaced_appears()
    test_fiction_dedup_user_entry_wins()
    test_fiction_mood_request_untouched()
    test_fiction_injected_carries_metadata_and_pinned()
    test_fiction_fuzzy_resolves_variant_to_canonical()
    test_fuzzy_does_not_overmatch()
    test_nonfiction_typed_title_in_library_appears()
    test_nonfiction_typed_title_not_surfaced_appears()
    test_nonfiction_mood_request_untouched()
    print("=" * 60)
    total = len(_PASS) + len(_FAIL)
    if _FAIL:
        print(f"  {len(_FAIL)}/{total} FAILED: {_FAIL}")
        return 1
    print(f"  ALL {total} CHECKS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
