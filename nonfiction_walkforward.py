"""
nonfiction_walkforward.py
=========================
DIRECTIONAL, NON-AUTHORITATIVE nonfiction backtest (leave-one-out).

Why LOO, not chronological: walkforward.py (fiction) trains on past-only folds with a
burn-in of 15; nonfiction has n=6 finished books, so a chronological harness would
evaluate ZERO folds. Leave-one-out gives all n folds — still tiny, still
non-authoritative, but enough for a BEFORE/AFTER sanity check that a schema/weight
change did not blow up predictability or band coverage. This is NOT a real accuracy
baseline (n=6); it is a guardrail.

Reads only. ZERO API spend, structurally: it never imports anthropic and never
researches — it uses nonfiction_engine.predict_nonfiction (pure analog math over the
already-rated books). Schema-agnostic: it discovers components/categories/weights from
the DB via load_nonfiction_from_db, so the SAME script runs on the old 8-component
schema and the new 12-component schema — which is exactly what makes the before/after
comparison honest to run (the numbers themselves are non-authoritative).

Caveat baked in: when the schema changes, the WA/Total-Average TARGET changes too, so
before/after MAE compares the predictability of two different targets, not one target
under two engines. Read it as "did the new schema stay as self-consistent / as coverable
as the old," not "is the new schema more accurate."

Run: python3 nonfiction_walkforward.py
"""

import numpy as np
import pandas as pd

import nonfiction_engine as ne


def leave_one_out(path="books.db", z=1.645):
    """One fold per finished nonfiction book: predict it from the other n-1 via the
    analog path, compare to its own rated WA / Total Average, and check whether the
    actual WA falls inside the served +/- z*sd band. Returns a per-book DataFrame."""
    books, gw, gcw = ne.load_nonfiction_from_db(path)
    cat_components = books.attrs.get("category_components", {})
    all_components = books.attrs.get("all_components", [])

    df = books[books["Status"].fillna("finished") == "finished"].reset_index(drop=True)
    df.attrs["category_components"] = cat_components
    df.attrs["all_components"] = all_components
    bt_full = ne.add_total_average(df)

    rows = []
    for i in range(len(df)):
        held = df.iloc[i]
        rest = df.drop(df.index[i]).reset_index(drop=True)
        rest.attrs["category_components"] = cat_components
        rest.attrs["all_components"] = all_components

        p = ne.predict_nonfiction(held["Book"], held["Author"],
                                  held.get("Genre"), (rest, gw, gcw), z=z)
        act_wa = float(held["WA"]) if pd.notna(held["WA"]) else np.nan
        act_ta = float(bt_full.iloc[i]["Total Average"])
        lo, hi = p["wa_ci"]
        rows.append({
            "Book": (held["Book"] or "")[:32],
            "pred_WA": round(p["wa_final"], 3),
            "act_WA": round(act_wa, 3),
            "err_WA": round(p["wa_final"] - act_wa, 3),
            "pred_TA": round(p["total_avg_est"], 3),
            "act_TA": round(act_ta, 3),
            "err_TA": round(p["total_avg_est"] - act_ta, 3),
            "band": f"[{lo:.2f},{hi:.2f}]",
            "in_band": bool(lo <= act_wa <= hi) if not np.isnan(act_wa) else False,
        })
    out = pd.DataFrame(rows)
    out.attrs["n_components"] = len(all_components)
    out.attrs["components"] = list(all_components)
    out.attrs["categories"] = list(cat_components.keys())
    return out


def report(path="books.db"):
    res = leave_one_out(path)
    n = len(res)
    mae_wa = float(res["err_WA"].abs().mean()) if n else float("nan")
    mae_ta = float(res["err_TA"].abs().mean()) if n else float("nan")
    cov = float(res["in_band"].mean()) if n else float("nan")

    print("=" * 74)
    print(f"NONFICTION LEAVE-ONE-OUT  —  n={n}   (directional, NON-authoritative)")
    print("=" * 74)
    print(f"schema: {res.attrs['n_components']} components across "
          f"{len(res.attrs['categories'])} categories "
          f"({', '.join(res.attrs['categories'])})")
    print(f"components: {', '.join(res.attrs['components'])}\n")
    print(res.to_string(index=False))
    print()
    print(f"  MAE(WA)          : {mae_wa:.3f}")
    print(f"  MAE(Total Avg)   : {mae_ta:.3f}")
    print(f"  band coverage    : {cov:.0%}   (+/- {1.645}·sd band — NOT the conformal band)")
    print(f"\n  ** n={n}: non-authoritative. Guardrail only, not an accuracy baseline. **")
    return {"n": n, "mae_wa": mae_wa, "mae_ta": mae_ta, "coverage": cov}


if __name__ == "__main__":
    report()
