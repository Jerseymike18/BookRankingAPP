#!/usr/bin/env python3
"""
source_book_text.py — Phase 3 Branch A, step 1: source per-book descriptive text.
For each rated fiction book, get a concise, spoiler-free premise/themes/tone
description (the taste-relevant signal a reader sees BEFORE reading), and cache it
through db_write.put_book_text (isolated file validation/book_text.db) + a
committed JSON mirror.

Cheap Sonnet (claude-sonnet-4-6), one call/book, NO web_search. Resumable (skips
cached). Credit-safe: a credit-balance 400 is terminal — it aborts with a resume
message and never reports a partial run (house rule).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import anthropic
import db_loader
import db_write
import research_layer as rl

MODEL = "claude-sonnet-4-6"      # DISCOVER_MODEL (cheap); research_predict.py:51
MIRROR = os.path.join(ROOT, "validation", "book_descriptions.json")

SYS = ("You write concise, factual, spoiler-free book descriptions — premise/jacket "
       "copy a reader sees BEFORE reading. No plot spoilers, no ratings, and no claims "
       "about quality, acclaim, or reception.")


def _prompt(title, author, genre):
    return (f"Describe the book \"{title}\" by {author} (genre: {genre}) in about 90 words: "
            "its premise/setup, central themes, tone and mood, and prose style / subgenre "
            "feel. Spoiler-free. Do not say how good it is. Description only.")


def _mirror():
    json.dump(db_write.get_book_text(), open(MIRROR, "w", encoding="utf-8"),
              indent=2, sort_keys=True, ensure_ascii=False)


def main():
    books, _, _ = db_loader.load_from_db()
    have = db_write.get_book_text()
    todo = [(r["Book"], r["Author"], r["Genre"]) for _, r in books.iterrows()
            if r["Book"] not in have]
    print(f"{len(have)} cached, {len(todo)} to source (of {len(books)}).")
    if not todo:
        print("All books have text. Nothing to do.")
        return

    client = anthropic.Anthropic(api_key=rl.load_key("apikey.txt"))
    done, failed = 0, 0
    for title, author, genre in todo:
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=256, system=SYS,
                messages=[{"role": "user", "content": _prompt(title, author, genre)}])
            text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        except Exception as e:
            if rl.is_out_of_credits(e):
                _mirror()
                print(f"\n✗ OUT OF CREDITS after {done} sourced this run. Aborting (no partial "
                      f"metrics). Top up, then re-run — it resumes from {done + len(have)}/{len(books)}.")
                raise SystemExit(2)
            print(f"  ! {title[:40]}: {type(e).__name__} — skipped")
            failed += 1
            continue
        if not text:
            print(f"  ! {title[:40]}: empty response — skipped")
            failed += 1
            continue
        db_write.put_book_text(title, text, source=f"llm:{MODEL}")
        done += 1
        _mirror()
        if done % 10 == 0:
            print(f"  … {done}/{len(todo)}")
    print(f"\nSourced {done}, failed {failed}. Total cached: {len(db_write.get_book_text())}/{len(books)}.")
    print(f"  mirror: {MIRROR}")


if __name__ == "__main__":
    main()
