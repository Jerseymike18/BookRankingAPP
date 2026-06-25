"""
wa_rollup.py  (v2 — schema-agnostic)
====================================
Correctness check: confirm the Python WA roll-up reproduces your spreadsheet's
Weighted Average exactly, for every scored book.

v2 uses the header-driven loader from predict_engine.py, so it adapts to your
14-component layout (or any future layout) automatically — no hardcoded columns.

It rebuilds each book's WA from its stored weighted category averages
(WStoryAvg, WCharAvg, WAesAvg, WThemeAvg, WWBAvg) times the GenreWeights, and
compares to the stored Weighted Average. Every book should match to the penny.

Run in Thonny (needs predict_engine.py in the same folder).
"""
import numpy as np
import predict_engine as pe

WORKBOOK = pe.WORKBOOK


def main():
    books, gw, gcw = pe.load_everything(WORKBOOK)
    print("=" * 60)
    print("WA ROLL-UP CORRECTNESS CHECK (Python vs. spreadsheet)")
    print("=" * 60)
    print(f"Checking {len(books)} scored books...\n")

    cats = ["Story", "Character", "Theme", "Aesthetics", "Worldbuilding"]
    diffs = []
    mism = []
    for _, b in books.iterrows():
        g = b["Genre"]
        if g not in gw:
            continue
        w = gw[g]
        # WA = sum of weighted category average * genre weight
        wa = (b["WStory"] * (w["Story"] or 0) +
              b["WCharacter"] * (w["Character"] or 0) +
              b["WTheme"] * (w["Theme"] or 0) +
              b["WAesthetics"] * (w["Aesthetics"] or 0) +
              b["WWorldbuilding"] * (w["Worldbuilding"] or 0))
        d = abs(wa - b["WA"])
        diffs.append(d)
        if d > 1e-6:
            mism.append((b["Book"], g, b["WA"], wa, d))

    matches = sum(1 for d in diffs if d <= 1e-6)
    print(f"  Exact matches: {matches} / {len(diffs)}")
    if not mism:
        print("\n  *** ALL BOOKS MATCH — Python WA reproduces your sheet. ***")
    else:
        print(f"\n  {len(mism)} mismatches:")
        for book, g, excel, py, d in sorted(mism, key=lambda x: -x[4])[:15]:
            print(f"   {book[:34]:<34} {g[:16]:<16} Excel={excel:.4f} Py={py:.4f} Δ={d:.4f}")


if __name__ == "__main__":
    main()
