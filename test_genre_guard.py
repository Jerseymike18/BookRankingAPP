"""
test_genre_guard.py — an unscoreable genre must be REFUSED, never scored as 0.00.

WHY THIS EXISTS
---------------
Every WA roll-up in this codebase reads the genre's weights defensively:

    wa += wcat * (gw.get(genre, {}).get(cat, 0) or 0)

so a genre with no `genre_weights` row does not raise. It contributes zero from
every category, and the book comes back with a full, confident-looking component
breakdown and a **WA of 0.00**. That is the worst shape a bug can take: it looks
like an answer, and nothing anywhere says otherwise.

Found 2026-08-21 by calling /api/demo/predict with genre "Science Fiction" — a
name that reads as obviously valid but is not in the table, which holds
"Science Fiction (Hard)" and "Science Fiction (Soft)". The response carried real
component scores and wa 0.0.

Every OTHER route into the engine was already guarded — LLM genre detection
(research_predict), Discover candidate generation, the single-book injection's
allowed-set check, and db_write.add_book, which refuses an unknown genre outright.
A genre supplied directly by the CALLER on the two research endpoints was the one
unchecked door. These checks pin it shut, and pin the reason shut with it: the
first check below demonstrates the silent-zero behaviour that makes the guard
necessary, so deleting the guard cannot look harmless.

The rejection happens BEFORE the research call, so this spends no Anthropic
credits and needs no key — the endpoint returns 422 without ever reaching the LLM.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib

main = importlib.import_module("backend.main")

FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


CATS = ["Story", "Character", "Theme", "Aesthetics", "Worldbuilding"]


def run():
    print("\n=== GENRE GUARD ===\n")

    # ── 1. The hazard itself, so the guard's reason is executable ────────────
    gw = {"Epic Fantasy": {c: 0.2 for c in CATS}}
    wcats = {c: 8.0 for c in CATS}          # a strong book by every category
    known = sum(wcats[c] * (gw.get("Epic Fantasy", {}).get(c, 0) or 0) for c in CATS)
    unknown = sum(wcats[c] * (gw.get("Science Fiction", {}).get(c, 0) or 0) for c in CATS)
    check("a KNOWN genre rolls up to a real WA", round(known, 2) == 8.0, f"wa={known}")
    check("an UNKNOWN genre silently rolls up to 0.00 (this is why the guard exists)",
          unknown == 0.0, f"wa={unknown}")

    # ── 2. The guard ────────────────────────────────────────────────────────
    allowed = ["Epic Fantasy", "Science Fiction (Hard)", "Science Fiction (Soft)"]
    check("guard accepts a genre that has weights",
          main._genre_has_weights("Epic Fantasy", allowed))
    check("guard rejects the plausible-but-absent name that caused this",
          not main._genre_has_weights("Science Fiction", allowed))
    check("guard rejects a near-miss / casing variant",
          not main._genre_has_weights("epic fantasy", allowed)
          and not main._genre_has_weights("Epic  Fantasy", allowed))
    check("guard rejects empty and None rather than treating them as wildcards",
          not main._genre_has_weights("", allowed)
          and not main._genre_has_weights(None, allowed))

    # ── 3. Both endpoints actually consult it ───────────────────────────────
    # Cheap structural check: the guard is only worth anything if it is wired in,
    # and both call sites are easy to drop in a refactor without any test noticing.
    import inspect
    for fn_name in ("predict_research", "demo_predict"):
        src = inspect.getsource(getattr(main, fn_name))
        check(f"{fn_name} consults the guard", "_genre_has_weights" in src)

    # The authenticated endpoint must reject BEFORE spending the research call —
    # rejecting afterwards would still pay Anthropic to produce a guaranteed 0.00.
    src = inspect.getsource(main.predict_research)
    check("predict_research rejects BEFORE the research call (spends nothing)",
          src.index("_genre_has_weights") < src.index("research_book"),
          "guard must precede _rp.research_book")

    print()
    if FAILED:
        print(f"  {len(FAILED)} CHECK(S) FAILED")
        for f in FAILED:
            print(f"    - {f}")
        return 1
    print("  ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
