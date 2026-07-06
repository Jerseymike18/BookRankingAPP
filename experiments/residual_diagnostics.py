"""
residual_diagnostics.py
=======================
Read-only mechanism-level calibration report. Groups delta_log prediction
residuals by the mechanism metadata each row now carries — genre, author,
confidence, analog source, correction magnitude, CI width, word count, genre
grounding — to surface WHICH KINDS of predictions miss most, not just which
books. This is the diagnostic complement to the per-book delta_log: it turns
"this book was off" into "predictions of THIS shape are off".

NO WRITES. Reads books.db (delta_log) only; changes nothing, touches no engine
math. Safe to run any time.

SCOPE: only rows that carry mechanism metadata (pred_genre NOT NULL) are
analysed. The historical rows imported before the metadata upgrade (the workbook
backfill + any pre-upgrade live row) have NULL metadata and are EXCLUDED — never
relabeled. New live-logged predictions accrue metadata going forward, so every
breakdown fills in on its own as forecast books get read and rated.

RESIDUAL: d_wa = actual - predicted WA (the delta_log convention). Positive =
underprediction, negative = overprediction. "MAE" is mean |d_wa| (how far off,
either way); "bias" is mean d_wa (which direction). A KIND of prediction with a
high MAE is the least accurate; a large-magnitude bias means it leans one way.

THRESHOLDS — when a view becomes meaningful (also printed at runtime):
  * OVERALL   : >= 15 metadata rows before the aggregate is more than anecdotal.
  * A bucket  : >= 5 rows to report a number; 5-7 is provisional, >= 8 is
                trustworthy. Below 5 the bucket is shown but flagged "thin".
  * Authors   : sparse by nature — shown only at >= 3 rows (provisional until 5).
These are deliberately modest: this is one reader's library, not a benchmark.

HOW TO RUN
    python3 residual_diagnostics.py
"""

import math
import sqlite3

import db_write   # single source of truth for the DB path + metadata columns

MIN_OVERALL = 15   # fewer metadata rows than this: aggregate is anecdotal
MIN_GROUP   = 5    # a bucket needs >= this to report a trustworthy-ish number
MIN_TRUST   = 8    # >= this: trustworthy
MIN_AUTHOR  = 3    # authors are sparse; show at >= this, provisional until MIN_GROUP


def _summ(res):
    """(n, bias, MAE, RMSE) for a list of signed residuals."""
    n = len(res)
    if n == 0:
        return 0, None, None, None
    bias = sum(res) / n
    mae = sum(abs(x) for x in res) / n
    rmse = math.sqrt(sum(x * x for x in res) / n)
    return n, bias, mae, rmse


def _flag(n):
    if n >= MIN_TRUST:
        return ""
    if n >= MIN_GROUP:
        return "  (provisional)"
    return "  (thin)"


def _bucket_corr(v):
    if v is None:
        return "n/a"
    v = abs(v)
    if v < 0.1:
        return "0.0-0.1 negligible"
    if v < 0.3:
        return "0.1-0.3 mild"
    if v < 0.6:
        return "0.3-0.6 moderate"
    return ">0.6 heavy"


def _bucket_ci(v):
    if v is None:
        return "n/a"
    if v < 0.40:
        return "<0.40 tight"
    if v < 0.50:
        return "0.40-0.50"
    return ">=0.50 wide"


def _bucket_words(v):
    if v is None:
        return "n/a"
    if v < 100_000:
        return "<100k"
    if v < 250_000:
        return "100-250k"
    if v < 500_000:
        return "250-500k"
    return ">=500k"


def _bucket_ngenre(v):
    if v is None:
        return "n/a"
    if v < 5:
        return "<5 thin-genre"
    if v < 15:
        return "5-14"
    return ">=15 well-sampled"


def _load():
    con = sqlite3.connect(db_write.DB)
    total = con.execute("SELECT COUNT(*) FROM delta_log").fetchone()[0]
    rows = con.execute(
        "SELECT d_wa, pred_genre, pred_author, conf, analog_src, corr_wa, "
        "       ci_width, pred_words, n_genre, n_author, corr_method "
        "FROM delta_log "
        "WHERE pred_genre IS NOT NULL AND d_wa IS NOT NULL"
    ).fetchall()
    con.close()
    keys = ["d_wa", "genre", "author", "conf", "analog_src", "corr_wa",
            "ci_width", "words", "n_genre", "n_author", "corr_method"]
    return total, [dict(zip(keys, r)) for r in rows]


def _report_group(title, rows, keyfn, floor=1):
    """Group residuals by keyfn, print worst-MAE-first. `floor` hides groups with
    fewer than that many rows (used to keep the long-tail author view readable)."""
    groups = {}
    for r in rows:
        groups.setdefault(keyfn(r), []).append(r["d_wa"])
    stats = []
    for g, res in groups.items():
        n, bias, mae, _ = _summ(res)
        stats.append((mae, n, bias, g))
    stats.sort(reverse=True)  # highest MAE (least accurate KIND) first

    print(f"\n{title}")
    print(f"  {'group':26}{'n':>4}{'bias':>9}{'MAE':>8}")
    shown = hidden = 0
    for mae, n, bias, g in stats:
        if n < floor:
            hidden += 1
            continue
        print(f"  {str(g):26}{n:>4}{bias:>+9.3f}{mae:>8.3f}{_flag(n)}")
        shown += 1
    if shown == 0:
        print(f"  (no group with >= {floor} row(s) yet)")
    if hidden:
        print(f"  (+{hidden} group(s) below the {floor}-row display floor)")


def main():
    total, rows = _load()
    n = len(rows)
    print("=" * 68)
    print("RESIDUAL DIAGNOSTICS  —  where prediction error concentrates")
    print("=" * 68)
    print(f"delta_log: {total} rows total, {n} carry mechanism metadata "
          f"({total - n} pre-upgrade, excluded — never relabeled).")
    print("Residual d_wa = actual - predicted WA  (+ under-, - over-predicted).")
    print(f"Thresholds: overall >= {MIN_OVERALL}; bucket >= {MIN_GROUP} to report "
          f"({MIN_GROUP}-{MIN_TRUST - 1} provisional, >= {MIN_TRUST} solid); "
          f"authors >= {MIN_AUTHOR}.")

    if n == 0:
        print("\nNo mechanism-tagged rows yet — the 126 historical rows predate the "
              "upgrade. Each newly-read forecast book adds one tagged row; every "
              "breakdown below activates automatically as they accrue.")
        return

    N, bias, mae, rmse = _summ([r["d_wa"] for r in rows])
    tag = "" if N >= MIN_OVERALL else "   << anecdotal (below overall threshold)"
    print(f"\nOVERALL   n={N}   bias={bias:+.3f}   MAE={mae:.3f}   "
          f"RMSE={rmse:.3f}{tag}")

    # Dimensions the task calls out: genre, author, confidence, and — the
    # mechanism levers — analog source, correction magnitude, CI width, plus
    # word count and genre grounding for good measure.
    _report_group("BY GENRE", rows, lambda r: r["genre"] or "Unknown")
    _report_group("BY AUTHOR (sparse)", rows,
                  lambda r: r["author"] or "Unknown", floor=MIN_AUTHOR)
    _report_group("BY CONFIDENCE", rows, lambda r: r["conf"] or "unknown")
    _report_group("BY ANALOG SOURCE", rows, lambda r: r["analog_src"] or "n/a")
    _report_group("BY CORRECTION MAGNITUDE |corr_wa|", rows,
                  lambda r: _bucket_corr(r["corr_wa"]))
    _report_group("BY CI WIDTH (confidence)", rows,
                  lambda r: _bucket_ci(r["ci_width"]))
    _report_group("BY WORD COUNT", rows, lambda r: _bucket_words(r["words"]))
    _report_group("BY GENRE GROUNDING (n_genre)", rows,
                  lambda r: _bucket_ngenre(r["n_genre"]))

    print("\nReading it: the top row of each block is the least-accurate KIND of "
          "prediction. A large |bias| there says the mechanism leans one way "
          "(candidate for a correction retune); a high MAE with ~0 bias says it "
          "is noisy, not biased. Concentrated error points the next calibration "
          "pass at a mechanism, not at individual books.")


if __name__ == "__main__":
    main()
