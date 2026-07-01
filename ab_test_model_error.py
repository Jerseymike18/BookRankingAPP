"""
ab_test_model_error.py
======================
A/B test: raw (pre-correction) component-scoring error
  Model A: claude-sonnet-4-5
  Model B: claude-opus-4-8

Calls the grounded research pipeline at the lowest level — rm.rich_prompt +
direct Anthropic API — bypassing the production cache entirely.
Read-only: does not touch books.db, research_cache.json, or any production file.

Usage:
    python ab_test_model_error.py

Output:
    - Per-component MAE/bias table printed to stdout
    - ab_test_results.csv saved to the project root (checkpointed per book)
    - Resumable: books already in CSV are skipped on restart
"""

import sys
import csv
import time
import random
import traceback

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

import anthropic
import db_loader
import reresearch_and_measure as rm
import research_layer as rl

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEED       = 42
MODEL_A    = "claude-sonnet-4-5"
MODEL_B    = "claude-opus-4-8"
OUTPUT_CSV = "ab_test_results.csv"

REALIST_GENRES = {"Literary Fiction", "Russian Literature", "Classical Epic",
                  "Gothic Fiction", "Classical Drama"}

# Token prices ($ per 1M tokens, in/out)
PRICES = {
    MODEL_A: (3.0,  15.0),
    MODEL_B: (15.0, 75.0),
}

# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

def call_with_retry(fn, max_retries=5):
    """Call fn(); retry on 429/5xx with exponential backoff."""
    delay = 5
    for attempt in range(max_retries):
        try:
            return fn()
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            print(f"  [rate-limit] sleeping {delay}s …")
            time.sleep(delay)
            delay = min(delay * 2, 120)
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 and attempt < max_retries - 1:
                print(f"  [server-error {e.status_code}] sleeping {delay}s …")
                time.sleep(delay)
                delay = min(delay * 2, 120)
            else:
                raise


# ---------------------------------------------------------------------------
# Raw LLM call — bypasses cache, accepts explicit model string
# ---------------------------------------------------------------------------

def call_model_raw(client, title, author, genre, model):
    """Call the rich research prompt directly. Returns (scores_dict, conf, usage)."""
    def _call():
        prompt = rm.rich_prompt(title, author, genre)
        msg = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        data = rl._extract_json(msg.content[0].text)
        conf = data.pop("confidence", "unknown")
        scores = {c: float(data[c]) for c in rm.LIVE if c in data}
        return scores, conf, msg.usage

    return call_with_retry(_call)


# ---------------------------------------------------------------------------
# WA rollup — same math as db_loader / research_predict._wa_from_components
# ---------------------------------------------------------------------------

def wa_from_raw(scores, genre, gw, gcw):
    wa = 0.0
    for cat in db_loader.CATEGORY_OF_INTEREST:
        wcat = db_loader._weighted_cat_avg(scores, genre, cat, gcw)
        wa += wcat * (gw.get(genre, {}).get(cat, 0) or 0)
    return wa


# ---------------------------------------------------------------------------
# CSV helpers — append-safe checkpointing
# ---------------------------------------------------------------------------

def load_existing_results(path):
    """Return list of result dicts already written to CSV, or [] if file absent."""
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def append_rows(path, rows, fieldnames):
    """Append rows to CSV, writing header only if file is new."""
    import os
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------

def estimate_cost(n_books):
    """Print a rough cost estimate based on pilot per-book token rates."""
    # Pilot observed ~1,200 input + ~120 output tokens per call (approximate)
    est_in  = 1_200
    est_out = 120
    total = 0.0
    print("\n  Pre-run cost estimate (approximate, based on pilot per-book rates):")
    for m, (pin, pout) in PRICES.items():
        cost = n_books * (est_in * pin + est_out * pout) / 1_000_000
        total += cost
        print(f"    {m:<30}  ~${cost:.2f}")
    print(f"    {'TOTAL (both models)':<30}  ~${total:.2f}")
    print()


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def comp_stats(rows, comp):
    errs = [float(r[f"err_{comp}"]) for r in rows if r.get(f"err_{comp}") not in (None, "", "None")]
    if not errs:
        return None, None
    return float(np.mean(np.abs(errs))), float(np.mean(errs))


def overall_mae_rows(rows, exclude=None):
    exclude = exclude or set()
    maes = []
    for c in rm.LIVE:
        if c in exclude:
            continue
        mae, _ = comp_stats(rows, c)
        if mae is not None:
            maes.append(mae)
    return float(np.mean(maes)) if maes else float("nan")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    books_df, gw, gcw = db_loader.load_from_db()

    # Build actual scores lookup
    actual_scores = {}
    actual_wa     = {}
    for _, row in books_df.iterrows():
        title = row["Book"]
        actual_scores[title] = {
            c: float(row[c]) if not (isinstance(row[c], float) and np.isnan(row[c])) else None
            for c in rm.LIVE
        }
        actual_wa[title] = float(row["WA"])

    # Full library — all rated books
    all_books = [row for _, row in books_df.iterrows()]
    rng = random.Random(SEED)
    rng.shuffle(all_books)  # deterministic order; seed used only here

    n_books = len(all_books)
    n_total = n_books * 2

    print(f"\n{'='*64}")
    print(f"  A/B TEST (FULL LIBRARY): {MODEL_A}  vs  {MODEL_B}")
    print(f"  {n_books} books  |  {n_total} API calls  |  cache bypassed")
    print(f"{'='*64}")

    estimate_cost(n_books)

    # Load already-completed results for resumability
    existing = load_existing_results(OUTPUT_CSV)
    done_keys = {(r["model"], r["title"]) for r in existing}
    print(f"  Resuming: {len(done_keys)} book/model pairs already in CSV, skipping those.\n")

    # Determine CSV fieldnames from a dummy record shape
    _dummy_comp_keys = []
    for c in rm.LIVE:
        _dummy_comp_keys += [f"raw_{c}", f"actual_{c}", f"err_{c}"]
    FIELDNAMES = (
        ["model", "title", "author", "genre", "is_realist", "conf",
         "pred_wa", "actual_wa", "wa_err"]
        + _dummy_comp_keys
    )

    client = anthropic.Anthropic(api_key=rm.load_key())
    total_in_tokens  = {MODEL_A: 0, MODEL_B: 0}
    total_out_tokens = {MODEL_A: 0, MODEL_B: 0}

    call_num   = 0
    skipped    = 0

    for row in all_books:
        title  = row["Book"]
        author = row["Author"]
        genre  = row["Genre"]
        actuals    = actual_scores[title]
        act_wa     = actual_wa[title]
        is_realist = genre in REALIST_GENRES

        book_rows = []  # collect both model results before appending
        for model in [MODEL_A, MODEL_B]:
            if (model, title) in done_keys:
                skipped += 1
                continue

            call_num += 1
            remaining_new = n_total - len(done_keys) - skipped
            label = model.split("claude-")[1] if "claude-" in model else model
            print(f"[{call_num:>3}] {label:<18}  {title[:52]}")
            try:
                raw, conf, usage = call_model_raw(client, title, author, genre, model)
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()
                continue

            total_in_tokens[model]  += usage.input_tokens
            total_out_tokens[model] += usage.output_tokens

            pred_wa = wa_from_raw(raw, genre, gw, gcw)

            rec = {
                "model":      model,
                "title":      title,
                "author":     author,
                "genre":      genre,
                "is_realist": is_realist,
                "conf":       conf,
                "pred_wa":    pred_wa,
                "actual_wa":  act_wa,
                "wa_err":     pred_wa - act_wa,
            }
            for c in rm.LIVE:
                rec[f"raw_{c}"]    = raw.get(c)
                rec[f"actual_{c}"] = actuals.get(c)
                err = (
                    (raw.get(c) - actuals[c])
                    if (raw.get(c) is not None and actuals.get(c) is not None)
                    else None
                )
                rec[f"err_{c}"] = err

            book_rows.append(rec)

        if book_rows:
            append_rows(OUTPUT_CSV, book_rows, FIELDNAMES)

    print(f"\nDone. Raw results saved → {OUTPUT_CSV}\n")

    # ── Reload full CSV for analysis (includes pre-existing rows) ─────────────
    all_results = load_existing_results(OUTPUT_CSV)

    def get_rows(model):
        return [r for r in all_results if r["model"] == model]

    rows_a = get_rows(MODEL_A)
    rows_b = get_rows(MODEL_B)

    # ── Per-component table ───────────────────────────────────────────────────
    print(f"{'Component':<22}  {'Sonnet MAE':>10}  {'Opus MAE':>8}  {'Δ (Opus-Son)':>12}  {'Son bias':>9}  {'Opus bias':>9}  {'Bias flip?':>10}")
    print("-" * 96)

    all_mae_a, all_mae_b = [], []
    wb_excluded = {"Worldbuilding"}  # for worldbuilding-excluded headline

    for c in rm.LIVE:
        mae_a, bias_a = comp_stats(rows_a, c)
        mae_b, bias_b = comp_stats(rows_b, c)
        delta = (mae_b - mae_a) if (mae_a is not None and mae_b is not None) else None

        # Bias flip: signs differ (and both non-negligible)
        flip = ""
        if bias_a is not None and bias_b is not None:
            if abs(bias_a) > 0.05 and abs(bias_b) > 0.05:
                if (bias_a > 0) != (bias_b > 0):
                    flip = "FLIP"
            elif abs(bias_a) > 0.05 and abs(bias_b) <= 0.05:
                flip = "→0"

        print(f"  {c:<20}  "
              f"{(f'{mae_a:>10.4f}') if mae_a is not None else '       N/A':>10}  "
              f"{(f'{mae_b:>8.4f}') if mae_b is not None else '     N/A':>8}  "
              f"{(f'{delta:>+12.4f}') if delta is not None else '          N/A':>12}  "
              f"{(f'{bias_a:>+9.4f}') if bias_a is not None else '      N/A':>9}  "
              f"{(f'{bias_b:>+9.4f}') if bias_b is not None else '      N/A':>9}  "
              f"{flip:>10}")
        if mae_a is not None: all_mae_a.append(mae_a)
        if mae_b is not None: all_mae_b.append(mae_b)

    print("-" * 96)
    overall_a = float(np.mean(all_mae_a)) if all_mae_a else float("nan")
    overall_b = float(np.mean(all_mae_b)) if all_mae_b else float("nan")
    print(f"  {'OVERALL MAE':<20}  {overall_a:>10.4f}  {overall_b:>8.4f}  {overall_b-overall_a:>+12.4f}")

    # Worldbuilding-excluded overall MAE
    wb_exc_a = overall_mae_rows(rows_a, exclude=wb_excluded)
    wb_exc_b = overall_mae_rows(rows_b, exclude=wb_excluded)
    print(f"  {'OVERALL MAE (no WB)':<20}  {wb_exc_a:>10.4f}  {wb_exc_b:>8.4f}  {wb_exc_b-wb_exc_a:>+12.4f}")

    # WA MAE
    wa_errs_a = [float(r["wa_err"]) for r in rows_a if r.get("wa_err") not in (None, "", "None")]
    wa_errs_b = [float(r["wa_err"]) for r in rows_b if r.get("wa_err") not in (None, "", "None")]
    wa_mae_a  = float(np.mean(np.abs(wa_errs_a))) if wa_errs_a else float("nan")
    wa_mae_b  = float(np.mean(np.abs(wa_errs_b))) if wa_errs_b else float("nan")
    wa_bias_a = float(np.mean(wa_errs_a)) if wa_errs_a else float("nan")
    wa_bias_b = float(np.mean(wa_errs_b)) if wa_errs_b else float("nan")
    print(f"  {'WA MAE':<20}  {wa_mae_a:>10.4f}  {wa_mae_b:>8.4f}  {wa_mae_b-wa_mae_a:>+12.4f}  {wa_bias_a:>+9.4f}  {wa_bias_b:>+9.4f}")

    print()
    winner = "Opus" if overall_b < overall_a else ("Sonnet" if overall_a < overall_b else "tie")
    print(f"  Verdict: {'Opus reduces overall component MAE' if winner=='Opus' else ('Sonnet is better' if winner=='Sonnet' else 'No difference')}  "
          f"(Δ = {overall_b-overall_a:+.4f})")
    print(f"  WA:      {'Opus reduces WA MAE' if wa_mae_b < wa_mae_a else 'Sonnet lower WA MAE'}  "
          f"(Δ = {wa_mae_b-wa_mae_a:+.4f})")

    # ── Per-genre MAE breakdown ───────────────────────────────────────────────
    genres_seen = sorted({r["genre"] for r in all_results})
    print(f"\n{'─'*80}")
    print(f"  Per-genre MAE breakdown (all components)")
    print(f"  {'Genre':<35}  {'n':>3}  {'Sonnet':>8}  {'Opus':>8}  {'Δ':>8}")
    print(f"  {'─'*35}  {'─'*3}  {'─'*8}  {'─'*8}  {'─'*8}")
    for g in genres_seen:
        ra = [r for r in rows_a if r["genre"] == g]
        rb = [r for r in rows_b if r["genre"] == g]
        n  = len(ra)
        if n == 0:
            continue
        ga = overall_mae_rows(ra)
        gb = overall_mae_rows(rb)
        print(f"  {g:<35}  {n:>3}  {ga:>8.4f}  {gb:>8.4f}  {gb-ga:>+8.4f}")

    # ── Token usage / cost ────────────────────────────────────────────────────
    print(f"\n  Token usage (this run only — excludes pre-existing rows):")
    total_cost = 0.0
    for m in [MODEL_A, MODEL_B]:
        tin, tout = total_in_tokens[m], total_out_tokens[m]
        pin, pout = PRICES[m]
        cost = (tin * pin + tout * pout) / 1_000_000
        total_cost += cost
        print(f"    {m:<30}  in={tin:>6,}  out={tout:>5,}  ~${cost:.3f}")
    print(f"    {'TOTAL':<30}  {'':>15}  ~${total_cost:.3f}")
    print()


if __name__ == "__main__":
    main()
