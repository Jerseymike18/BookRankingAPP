"""
backfill_series_numbers.py
==========================
Two-pass LLM backfill + verification of series_number across both DB tables.

FILL pass  — rows where series IS set but series_number IS NULL (both tables).
VERIFY pass — recommendations where series_number IS NOT NULL and the series
              name is shared by 2+ recs (exclude Standalone / single-entry series).
              Only flags MISMATCHES; identical values are silently skipped.

Run from the project root:
    python backfill_series_numbers.py [--fill-only | --verify-only]

After reviewing the proposed-changes table the script prints, run again with
--approve-fills or --approve-mismatches (or both) to commit.
"""

import argparse
import sqlite3
import sys
import time
import os

import research_predict as rp
import research_layer as rl
import db_write

DB = db_write.DB

# ─── Series excluded from FILL (ordinals are ambiguous or unwanted) ───────────
_SKIP_SERIES = {
    "drenai saga",
    "hainish cycle",
    "discworld: standalone",
}

# ─── Manual corrections: (table, title_lower) → corrected value (or None=skip) ─
_CORRECTIONS: dict = {
    # Ender's Shadow: The Last Shadow is #6, not #5
    ("books",           "the last shadow"):          6,
    ("recommendations", "the last shadow"):          6,
    # Discworld Witches: Wyrd Sisters is #2, Equal Rites is #1
    ("recommendations", "wyrd sisters"):             2,
    # Realm of the Elderlings: sub-series numbering errors
    ("recommendations", "ship of destiny"):          6,
    ("recommendations", "dragon keeper"):            10,
    ("recommendations", "blood of dragons"):         13,
    # Riftwar Saga: Silverthorn is #3
    ("recommendations", "silverthorn"):              3,
    # Shadow Campaigns: The Shadow Throne is #2
    ("recommendations", "the shadow throne"):        2,
    # Foundation: Prelude is a retroactive prequel → 0.5
    ("recommendations", "prelude to foundation"):    0.5,
    # Zones of Thought: A Deepness in the Sky is #2 (Fire Upon the Deep is #1)
    ("recommendations", "a deepness in the sky"):   2,
    # Black Company: The Silver Spike is an interstitial → 3.5
    ("recommendations", "the silver spike"):         3.5,
}

# ─── LLM helpers ─────────────────────────────────────────────────────────────

def _llm_series_number(client, title: str, author: str, series: str) -> dict:
    """Ask the LLM for the reading-order position of title within series.

    Returns {"series_number": int, "confidence": "high"|"low"}.
    On any parse failure returns {"series_number": None, "confidence": "low"}.
    """
    prompt = (
        f'Return ONLY a JSON object with exactly these two keys:\n'
        f'  "series_number": the reading-order position of the book as an integer '
        f'(1-indexed; 0 if truly unknown)\n'
        f'  "confidence": "high" if you are certain, "low" if uncertain\n\n'
        f'Book: "{title}"\n'
        f'Author: {author or "unknown"}\n'
        f'Series: {series}\n\n'
        f'Use READING order (publication order unless the series has an official '
        f'recommended reading order, e.g. Malazan). Respond with raw JSON only.'
    )
    msg = client.messages.create(
        model=rp.rm.MODEL,
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    try:
        data = rl._extract_json(text)
        n = data.get("series_number")
        conf = data.get("confidence", "low")
        if n is None or not isinstance(n, int):
            return {"series_number": None, "confidence": "low"}
        return {"series_number": int(n) if n else None, "confidence": conf}
    except Exception:
        return {"series_number": None, "confidence": "low"}


# ─── DB queries ──────────────────────────────────────────────────────────────

def _get_fill_rows(con, table: str):
    """Rows needing a series_number: series set, not excluded, series_number NULL."""
    rows = con.execute(
        f"SELECT title, author, series FROM {table} "
        f"WHERE series IS NOT NULL AND trim(series) != '' "
        f"  AND LOWER(trim(series)) != 'standalone' AND series_number IS NULL "
        f"ORDER BY series, title"
    ).fetchall()
    return [r for r in rows if r[2].strip().lower() not in _SKIP_SERIES]


def _get_verify_rows(con):
    """Recs with existing series_number where series is shared by 2+ recs."""
    multi = {
        r[0] for r in con.execute(
            "SELECT series FROM recommendations "
            "WHERE series IS NOT NULL AND trim(series) != '' "
            "  AND series != 'Standalone' AND series_number IS NOT NULL "
            "GROUP BY series HAVING count(*) >= 2"
        )
    }
    if not multi:
        return []
    placeholders = ",".join("?" * len(multi))
    return con.execute(
        f"SELECT title, author, series, series_number FROM recommendations "
        f"WHERE series IN ({placeholders}) AND series_number IS NOT NULL "
        f"ORDER BY series, series_number",
        list(multi),
    ).fetchall()


# ─── Main logic ──────────────────────────────────────────────────────────────

def run_fill(client, con, tables=("books", "recommendations")):
    """Return list of fill proposals: {table, title, author, series, proposed, confidence}."""
    proposals = []
    for table in tables:
        rows = _get_fill_rows(con, table)
        if not rows:
            continue
        print(f"\n[FILL] {table}: {len(rows)} rows with NULL series_number — querying LLM...")
        for i, (title, author, series) in enumerate(rows, 1):
            print(f"  {i}/{len(rows)}  {title!r}  ({series})", end="  ", flush=True)
            result = _llm_series_number(client, title, author or "", series)
            n = result["series_number"]
            conf = result["confidence"]
            # Apply manual correction if one exists
            key = (table, title.lower())
            if key in _CORRECTIONS:
                corrected = _CORRECTIONS[key]
                if corrected != n:
                    print(f"→ {n} → CORRECTED to {corrected}")
                    n, conf = corrected, "manual"
                else:
                    print(f"→ {n}  conf={conf} (correction agrees)")
            else:
                flag = "" if conf == "high" and n else "⚠"
                print(f"→ {n}  conf={conf} {flag}")
            proposals.append({
                "table": table,
                "title": title,
                "author": author or "",
                "series": series,
                "stored": None,
                "proposed": n,
                "confidence": conf,
                "kind": "fill",
            })
            time.sleep(0.2)
    return proposals


def run_verify(client, con):
    """Return list of mismatch proposals for multi-book recs."""
    rows = _get_verify_rows(con)
    if not rows:
        print("\n[VERIFY] No multi-book rec series found — nothing to verify.")
        return []

    print(f"\n[VERIFY] recommendations: {len(rows)} rows in multi-book series — querying LLM...")
    mismatches = []
    for i, (title, author, series, stored) in enumerate(rows, 1):
        print(f"  {i}/{len(rows)}  {title!r}  ({series} #{stored})", end="  ", flush=True)
        result = _llm_series_number(client, title, author or "", series)
        n = result["series_number"]
        conf = result["confidence"]
        if n is not None and n != stored:
            print(f"→ MISMATCH: stored={stored}, LLM says {n}  conf={conf} ⚠")
            mismatches.append({
                "table": "recommendations",
                "title": title,
                "author": author or "",
                "series": series,
                "stored": stored,
                "proposed": n,
                "confidence": conf,
                "kind": "mismatch",
            })
        else:
            print(f"→ ok ({n})")
        time.sleep(0.2)
    return mismatches


def _dup_gap_warnings(proposals):
    """Per series, flag duplicate or gap ordinals in the proposed set."""
    from collections import defaultdict
    series_nums = defaultdict(list)
    for p in proposals:
        if p["proposed"] is not None and p["proposed"] > 0:
            series_nums[(p["table"], p["series"])].append(p["proposed"])
    warnings = {}
    for key, nums in series_nums.items():
        nums_sorted = sorted(nums)
        dups = len(nums) != len(set(nums))
        # Only flag gaps for integer sequences; floats like 0.5/3.5 are intentional
        int_nums = [n for n in nums_sorted if n == int(n)]
        expected = list(range(1, len(int_nums) + 1))
        gaps = int_nums != expected
        if dups or gaps:
            warnings[key] = {"nums": nums_sorted, "dups": dups, "gaps": gaps}
    return warnings


def _print_table(proposals, warnings):
    """Print a grouped, human-readable proposed-changes table."""
    from collections import defaultdict
    by_series = defaultdict(list)
    for p in proposals:
        by_series[(p["table"], p["series"])].append(p)

    print("\n" + "═" * 80)
    print("PROPOSED CHANGES")
    print("═" * 80)

    fill_count = sum(1 for p in proposals if p["kind"] == "fill" and p["proposed"])
    fill_skip  = sum(1 for p in proposals if p["kind"] == "fill" and not p["proposed"])
    mm_count   = sum(1 for p in proposals if p["kind"] == "mismatch")
    print(f"  Fills:      {fill_count} values to write  ({fill_skip} remain NULL — low-conf or unknown)")
    print(f"  Mismatches: {mm_count} stored values differ from LLM")
    print()

    for (table, series), rows in sorted(by_series.items()):
        warn_key = (table, series)
        extra = ""
        if warn_key in warnings:
            w = warnings[warn_key]
            parts = []
            if w["dups"]: parts.append("DUPLICATE numbers")
            if w["gaps"]: parts.append("gaps in sequence")
            extra = f"  ⚠ {', '.join(parts)}: {w['nums']}"
        print(f"  [{table}]  {series}{extra}")
        for p in sorted(rows, key=lambda x: (x["proposed"] or 0)):
            conf_flag = " ⚠low-conf" if p["confidence"] == "low" else ""
            if p["kind"] == "fill":
                val = str(p["proposed"]) if p["proposed"] else "— (skip)"
                print(f"    NULL → {val:>4}   {p['title']}{conf_flag}")
            else:
                print(f"    {p['stored']:>4} → {p['proposed']:>4}   {p['title']}{conf_flag}")
        print()

    print("═" * 80)


def _write_proposals(proposals, kinds):
    """Write approved proposals via db_write.set_series_number."""
    written = skipped = 0
    for p in proposals:
        if p["kind"] not in kinds:
            continue
        if not p["proposed"]:
            skipped += 1
            continue
        ok = db_write.set_series_number(p["table"], p["title"], p["proposed"])
        if ok:
            written += 1
        else:
            skipped += 1
    print(f"\n  Wrote {written} values, skipped {skipped}.")
    return written


def _final_report(con):
    print("\n" + "─" * 60)
    print("FINAL NULL COUNTS")
    for table in ("books", "recommendations"):
        total = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE series IS NOT NULL AND trim(series) != ''"
        ).fetchone()[0]
        nulls = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE series IS NOT NULL AND trim(series) != '' "
            f"AND series_number IS NULL"
        ).fetchone()[0]
        filled = total - nulls
        print(f"  {table}: {filled}/{total} filled, {nulls} still NULL")

    # Series with duplicate or gap ordinals in books
    for table in ("books", "recommendations"):
        rows = con.execute(
            f"SELECT series, GROUP_CONCAT(series_number ORDER BY series_number) "
            f"FROM {table} WHERE series IS NOT NULL AND series_number IS NOT NULL "
            f"GROUP BY series HAVING count(*) > 1"
        ).fetchall()
        problems = []
        for series, nums_str in rows:
            nums = [int(x) for x in nums_str.split(",") if x]
            if len(nums) != len(set(nums)) or sorted(nums) != list(range(1, len(nums)+1)):
                problems.append(f"    {series}: {nums}")
        if problems:
            print(f"\n  ⚠ {table} — series with duplicate/gap ordinals (fix by hand):")
            for p in problems:
                print(p)
    print()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill series_number via LLM")
    parser.add_argument("--fill-only",   action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--approve-fills",     action="store_true",
                        help="Write approved fill values (NULL → N)")
    parser.add_argument("--approve-mismatches", action="store_true",
                        help="Write approved mismatch overwrites (stored → LLM)")
    args = parser.parse_args()

    approve_mode = args.approve_fills or args.approve_mismatches

    # --- Schema must exist (db_write import triggers migration) ---------------
    # db_write already called _ensure_series_number() on import above.

    try:
        client = rp.get_client()
    except FileNotFoundError:
        sys.exit("apikey.txt not found — add your Anthropic API key to the project root.")

    con = sqlite3.connect(DB)

    do_fill   = not args.verify_only
    do_verify = not args.fill_only

    all_proposals = []

    if not approve_mode:
        # ── Proposal run ──────────────────────────────────────────────────────
        if do_fill:
            all_proposals += run_fill(client, con)
        if do_verify:
            all_proposals += run_verify(client, con)

        con.close()

        if not all_proposals:
            print("\nNothing to propose. All series_number columns look complete.")
            _final_report(sqlite3.connect(DB))
            return

        warnings = _dup_gap_warnings(all_proposals)
        _print_table(all_proposals, warnings)

        print("\nTo write approved values, re-run with:")
        if any(p["kind"] == "fill" for p in all_proposals):
            print("  python backfill_series_numbers.py --approve-fills")
        if any(p["kind"] == "mismatch" for p in all_proposals):
            print("  python backfill_series_numbers.py --approve-mismatches")
        print("(You can combine both flags in one run.)")
        print("\nNOTE: --approve-mismatches OVERWRITES existing stored values — review carefully.")

    else:
        # ── Approval run ──────────────────────────────────────────────────────
        # Re-query proposals fresh so the user knows exactly what's being written.
        kinds_to_write = set()
        if args.approve_fills:
            all_proposals += run_fill(client, con)
            kinds_to_write.add("fill")
        if args.approve_mismatches:
            all_proposals += run_verify(client, con)
            kinds_to_write.add("mismatch")

        con.close()

        if not all_proposals:
            print("\nNothing to write.")
        else:
            warnings = _dup_gap_warnings(all_proposals)
            _print_table(all_proposals, warnings)
            print(f"\nWriting approved kinds: {sorted(kinds_to_write)} ...")
            _write_proposals(all_proposals, kinds_to_write)

        _final_report(sqlite3.connect(DB))


if __name__ == "__main__":
    main()
