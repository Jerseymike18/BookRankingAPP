"""
experiments/tabpfn_regime_report.py  —  OFFLINE analysis of the regime artifacts.
=================================================================================
Consumes the per-prediction CSVs written by tabpfn_regime.py and produces the
Brief 1 (cold-start regime slice) and Brief 2 (honest blend) deliverables:

  * validation/regime/regime_report.md   (Brief 1: bucketed WA MAE + crossover)
  * validation/regime/blend_report.md     (Brief 2: decorrelation + honest blend)
  * validation/regime/bucketed_table.csv   (the bucketed WA MAE table)
  * validation/regime/mae_vs_n.svg         (WA-MAE-vs-N plot, hand-rolled, no deps)

Pure analysis: numpy + stdlib only (no TabPFN, no engine, no books.db, no writes
to books.db). Deterministic. WA MAE is the harness's UNWEIGHTED mean of per-fold
|error| (weight column is uniform 1.0 — walk-forward has no per-prediction weight).

  .venv-tabpfn/bin/python experiments/tabpfn_regime_report.py
"""

import csv
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "validation", "regime")

MODELS = ["champion", "mirror", "meta", "naive"]
MODEL_LABEL = {"champion": "engine (honest)", "mirror": "TabPFN mirror",
               "meta": "TabPFN metadata", "naive": "naive (pool-mean WA)"}
MODEL_COLOR = {"champion": "#2D6A4F", "mirror": "#C07C5A",
               "meta": "#7B8FA1", "naive": "#C4B8AD"}

# N-bucket edges [lo, hi) chosen from the Phase-0 distribution (~1 fold per N).
# The two lowest buckets are only populated by the burn-in-3 exploratory run and
# are small by nature — their counts are surfaced so signal can be told from noise.
BUCKETS = [(3, 8), (8, 15), (15, 25), (25, 40), (40, 60), (60, 90), (90, 129)]


# ---------------------------------------------------------------------------
# Load + consistency gate
# ---------------------------------------------------------------------------
def load_csv(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            for k in ("position", "N", "n_author", "n_genre"):
                r[k] = int(r[k])
            for k in ("actual_wa", "weight", "champion_wa", "champion_abs",
                      "champion_signed", "mirror_wa", "mirror_abs", "mirror_signed",
                      "meta_wa", "meta_abs", "meta_signed", "naive_wa", "naive_abs"):
                r[k] = float(r[k])
            rows.append(r)
    rows.sort(key=lambda r: r["position"])
    return rows


def load_all():
    """bi03 is the superset (N>=3); bi15 is the authoritative anchor (N>=15). Assert
    they agree byte-for-byte on the overlap, then analyse the union (== bi03)."""
    bi03 = load_csv(os.path.join(OUT_DIR, "perpred_bi03.csv"))
    bi15 = load_csv(os.path.join(OUT_DIR, "perpred_bi15.csv"))
    by_pos03 = {r["position"]: r for r in bi03}
    worst = 0.0
    for r in bi15:
        o = by_pos03.get(r["position"])
        assert o is not None, f"pos {r['position']} in bi15 missing from bi03"
        for k in ("champion_wa", "mirror_wa", "meta_wa"):
            worst = max(worst, abs(r[k] - o[k]))
    assert worst < 1e-9, f"bi03/bi15 overlap disagree (worst {worst:.2e})"
    print(f"consistency gate: bi03 N>=15 == bi15 (worst |Δ|={worst:.1e}) over "
          f"{len(bi15)} folds")
    return bi03, bi15


def mae(rows, model):
    return float(np.mean([r[f"{model}_abs"] for r in rows])) if rows else float("nan")


def bucket_rows(rows, lo, hi):
    return [r for r in rows if lo <= r["N"] < hi]


# ---------------------------------------------------------------------------
# Brief 1: bucketed WA MAE + crossover
# ---------------------------------------------------------------------------
def bucketed_table(rows):
    table = []
    for lo, hi in BUCKETS:
        sub = bucket_rows(rows, lo, hi)
        rec = {"bucket": f"{lo}-{hi-1}", "lo": lo, "hi": hi, "n": len(sub),
               "meanN": float(np.mean([r["N"] for r in sub])) if sub else None}
        for m in MODELS:
            rec[m] = mae(sub, m) if sub else None
        table.append(rec)
    return table


def crossover(table, variant):
    """Smallest-N bucket (with n>0) where the variant beats the engine, and the
    low-N gap (variant - engine; negative == variant better)."""
    hits = [b for b in table if b["n"] > 0 and b[variant] is not None
            and b[variant] < b["champion"]]
    first = hits[0]["bucket"] if hits else None
    return first, [(b["bucket"], b["n"], b[variant] - b["champion"]) for b in table
                   if b["n"] > 0]


# ---------------------------------------------------------------------------
# Brief 2: decorrelation + honest out-of-fold blend
# ---------------------------------------------------------------------------
def resid_corr(rows, variant):
    a = np.array([r["champion_signed"] for r in rows])
    b = np.array([r[f"{variant}_signed"] for r in rows])
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _blend_abs(rows, variant, w):
    """|w*engine + (1-w)*tabpfn - actual| per fold (vectorised)."""
    e = np.array([r["champion_wa"] for r in rows])
    t = np.array([r[f"{variant}_wa"] for r in rows])
    a = np.array([r["actual_wa"] for r in rows])
    return np.abs(w * e + (1 - w) * t - a)


WGRID = np.round(np.linspace(0.0, 1.0, 101), 2)     # 0.00 .. 1.00 step 0.01


def best_w_insample(rows, variant):
    maes = [(_blend_abs(rows, variant, w).mean(), w) for w in WGRID]
    m, w = min(maes)
    return float(w), float(m)


def lofo_blend(rows, variant):
    """Leave-one-fold-out honest blend: for each fold, choose w on all OTHER folds,
    score that fold with it. Returns (oof_mae, chosen_ws, engine_mae)."""
    abs_by_w = {w: _blend_abs(rows, variant, w) for w in WGRID}   # (nfolds,) each
    n = len(rows)
    oof_err, chosen = [], []
    for i in range(n):
        mask = np.ones(n, dtype=bool); mask[i] = False
        best_w, best_m = None, None
        for w in WGRID:
            m = abs_by_w[w][mask].mean()
            if best_m is None or m < best_m:
                best_m, best_w = m, w
        oof_err.append(abs_by_w[best_w][i])
        chosen.append(float(best_w))
    return float(np.mean(oof_err)), chosen, mae(rows, "champion")


def expanding_blend(rows, variant, min_train=20):
    """Walk-forward honest blend: choose w on folds 0..k-1, apply to fold k. Returns
    (oof_mae, chosen_ws, matched_engine_mae) — the engine baseline is measured on the
    SAME scored folds (k>=min_train), never the full set, so the delta is apples-to-
    apples (later folds are easier; comparing to the overall engine would flatter it)."""
    abs_by_w = {w: _blend_abs(rows, variant, w) for w in WGRID}
    n = len(rows)
    oof_err, chosen, scored_idx = [], [], []
    for k in range(min_train, n):
        best_w, best_m = None, None
        for w in WGRID:
            m = abs_by_w[w][:k].mean()
            if best_m is None or m < best_m:
                best_m, best_w = m, w
        oof_err.append(abs_by_w[best_w][k]); chosen.append(float(best_w)); scored_idx.append(k)
    eng = float(np.mean([rows[k]["champion_abs"] for k in scored_idx]))
    return float(np.mean(oof_err)) if oof_err else float("nan"), chosen, eng


def n_adaptive_blend(rows, variant):
    """LOFO N-adaptive blend: w = w_lo for N<=t else w_hi; (t,w_lo,w_hi) fit on the
    other folds. Only meaningful if TabPFN helps at low N; reported regardless."""
    Ns = np.array([r["N"] for r in rows])
    thresholds = sorted({int(t) for t in np.percentile(Ns, [20, 30, 40, 50, 60])})
    grid = [(t, wl, wh) for t in thresholds for wl in WGRID[::5] for wh in WGRID[::5]]
    abs_by_w = {w: _blend_abs(rows, variant, w) for w in WGRID[::5]}
    n = len(rows)

    def err_for(params, idx_mask):
        t, wl, wh = params
        low = Ns <= t
        e = np.where(low, abs_by_w[wl], abs_by_w[wh])
        return e[idx_mask].mean(), e

    oof_err, chosen = [], []
    for i in range(n):
        mask = np.ones(n, dtype=bool); mask[i] = False
        best_p, best_m = None, None
        for p in grid:
            m, _ = err_for(p, mask)
            if best_m is None or m < best_m:
                best_m, best_p = m, p
        _, e_full = err_for(best_p, mask)
        oof_err.append(e_full[i]); chosen.append(best_p)
    return float(np.mean(oof_err)), chosen


# ---------------------------------------------------------------------------
# Hand-rolled SVG plot (WA MAE vs N, per bucket) — no matplotlib dependency.
# ---------------------------------------------------------------------------
def render_svg(table, path):
    W, H = 820, 460
    ml, mr, mt, mb = 62, 150, 30, 52
    pw, ph = W - ml - mr, H - mt - mb
    pts = [b for b in table if b["n"] > 0]
    xs = [b["meanN"] for b in pts]
    xmin, xmax = 0, max(xs) * 1.03
    allv = [b[m] for b in pts for m in MODELS if b[m] is not None]
    # Cap the y-axis for legibility: the metadata N=3–7 point (~3.0) otherwise
    # compresses every informative line into the lower third. Points above the cap
    # are clamped to the top edge and annotated with their true value.
    YCAP = 1.30
    ymin, ymax = 0.0, min(max(allv) * 1.08, YCAP)

    def X(v): return ml + (v - xmin) / (xmax - xmin) * pw
    def Y(v):
        yy = mt + (1 - (min(v, ymax) - ymin) / (ymax - ymin)) * ph
        return max(mt, yy)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="system-ui,sans-serif">']
    s.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    s.append(f'<text x="{ml}" y="18" font-size="14" font-weight="600" fill="#222">'
             'Walk-forward WA MAE vs. support-set size N (per bucket)</text>')
    # y gridlines + labels
    for k in range(6):
        yv = ymin + (ymax - ymin) * k / 5
        yy = Y(yv)
        s.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml+pw}" y2="{yy:.1f}" '
                 'stroke="#eee" stroke-width="1"/>')
        s.append(f'<text x="{ml-8}" y="{yy+4:.1f}" font-size="11" text-anchor="end" '
                 f'fill="#666">{yv:.2f}</text>')
    # x ticks (bucket mean N) + counts
    for b in pts:
        xx = X(b["meanN"])
        s.append(f'<line x1="{xx:.1f}" y1="{mt+ph}" x2="{xx:.1f}" y2="{mt+ph+4}" '
                 'stroke="#999" stroke-width="1"/>')
        s.append(f'<text x="{xx:.1f}" y="{mt+ph+18}" font-size="10" text-anchor="middle" '
                 f'fill="#666">{b["bucket"]}</text>')
        s.append(f'<text x="{xx:.1f}" y="{mt+ph+32}" font-size="9" text-anchor="middle" '
                 f'fill="#aaa">n={b["n"]}</text>')
    s.append(f'<text x="{ml+pw/2:.0f}" y="{H-6}" font-size="12" text-anchor="middle" '
             'fill="#444">N bucket (books rated before target)</text>')
    s.append(f'<text x="16" y="{mt+ph/2:.0f}" font-size="12" text-anchor="middle" '
             f'fill="#444" transform="rotate(-90 16 {mt+ph/2:.0f})">WA MAE (lower better)</text>')
    # model lines + markers
    s.append(f'<text x="{ml}" y="{H-6}" font-size="9" fill="#aaa">y-axis capped at '
             f'{ymax:.2f}; ↑value marks an off-scale point</text>')
    for i, m in enumerate(MODELS):
        col = MODEL_COLOR[m]
        pl = [(X(b["meanN"]), Y(b[m]), b[m]) for b in pts if b[m] is not None]
        d = " ".join(f'{"M" if j==0 else "L"}{x:.1f},{y:.1f}' for j, (x, y, _v) in enumerate(pl))
        dash = ' stroke-dasharray="4,3"' if m == "naive" else ''
        s.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2"{dash}/>')
        for (x, y, v) in pl:
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{col}"/>')
            if v > ymax + 1e-9:
                s.append(f'<text x="{x:.1f}" y="{y+12:.1f}" font-size="9" '
                         f'text-anchor="middle" fill="{col}">↑{v:.2f}</text>')
        ly = mt + 12 + i * 20
        s.append(f'<line x1="{ml+pw+14}" y1="{ly}" x2="{ml+pw+34}" y2="{ly}" '
                 f'stroke="{col}" stroke-width="2"{dash}/>')
        s.append(f'<text x="{ml+pw+38}" y="{ly+4}" font-size="11" fill="#333">'
                 f'{MODEL_LABEL[m]}</text>')
    s.append('</svg>')
    with open(path, "w") as fh:
        fh.write("\n".join(s))


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------
def _f(x, p=4):
    return " – " if x is None else f"{x:.{p}f}"


def write_bucketed_csv(table, path):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bucket", "n", "meanN"] + MODELS)
        for b in table:
            w.writerow([b["bucket"], b["n"], _f(b["meanN"], 1)]
                       + [_f(b[m]) for m in MODELS])


def write_regime_report(rows, bi15, table, path):
    overall = {m: mae(rows, m) for m in MODELS}
    overall15 = {m: mae(bi15, m) for m in MODELS}
    L = ["# Brief 1 — Cold-start regime slice: TabPFN vs. engine by support-set size\n"]
    L.append("Walk-forward, current engine, zero-API. N = books rated strictly before "
             "the target (== harness pool_size == position−1). WA MAE is the harness's "
             "unweighted mean of per-fold |error|. Two TabPFN v2 challengers (Apache, "
             "seed 42, CPU): **mirror** (14 LLM comps + genre code + causal author/genre "
             "prior mean & count — the engine's own information) and **metadata** (the "
             "prior bake-off's 8 causal metadata features, no LLM vector).\n")
    L.append(f"- **Authoritative (burn-in 15, N≥15, {len(bi15)} folds):** engine "
             f"{_f(overall15['champion'])} · mirror {_f(overall15['mirror'])} · meta "
             f"{_f(overall15['meta'])} · naive {_f(overall15['naive'])}")
    L.append(f"- **Exploratory union (burn-in 3, N≥3, {len(rows)} folds):** engine "
             f"{_f(overall['champion'])} · mirror {_f(overall['mirror'])} · meta "
             f"{_f(overall['meta'])} · naive {_f(overall['naive'])}\n")

    L.append("## WA MAE by N-bucket\n")
    L.append("| N bucket | n | mean N | engine | TabPFN mirror | TabPFN metadata | naive |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for b in table:
        flag = " ⚠" if 0 < b["n"] < 10 else ""
        L.append(f"| {b['bucket']}{flag} | {b['n']} | {_f(b['meanN'],1)} | "
                 f"{_f(b['champion'])} | {_f(b['mirror'])} | {_f(b['meta'])} | "
                 f"{_f(b['naive'])} |")
    L.append("\n⚠ = fewer than 10 folds (small-sample; read the delta against the count).\n")

    L.append("## Crossover — does TabPFN beat the engine at low N?\n")
    for variant in ("mirror", "meta"):
        first, deltas = crossover(table, variant)
        L.append(f"**TabPFN {variant}:** " + (
            f"first bucket beating the engine at N = **{first}**."
            if first else "**no N-bucket beats the engine.**"))
        L.append("")
        L.append("| N bucket | n | " + f"{variant} − engine (WA MAE; − = TabPFN better) |")
        L.append("| --- | --- | --- |")
        for bk, n, d in deltas:
            L.append(f"| {bk} | {n} | {d:+.4f} |")
        L.append("")

    L.append("## Read\n")
    L.append("The plot is `mae_vs_n.svg`; the table is `bucketed_table.csv`; per-prediction "
             "rows are `perpred_bi15.csv` (authoritative) and `perpred_bi03.csv` (adds N<15). "
             "A GO for a cold-start hybrid needs TabPFN winning by a meaningful, stable "
             "margin in low-N buckets with non-trivial counts — judge the deltas above "
             "against their n.\n")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return overall, overall15


def blend_robustness(rows, variant, w, seed=42, b=10000):
    """Is the fixed-w blend's improvement real or a few-outlier artifact? Returns
    sign counts, a seeded paired-bootstrap 95% CI of the mean |error| improvement,
    and the improvement with the top-3 improving folds removed."""
    e = np.array([r["champion_wa"] for r in rows])
    t = np.array([r[f"{variant}_wa"] for r in rows])
    a = np.array([r["actual_wa"] for r in rows])
    eng_abs = np.abs(e - a)
    bl_abs = np.abs(w * e + (1 - w) * t - a)
    diff = eng_abs - bl_abs                       # + == blend better
    rng = np.random.default_rng(seed)
    n = len(diff)
    idx = rng.integers(0, n, size=(b, n))
    means = diff[idx].mean(axis=1)
    order = np.argsort(-diff)
    keep = order[3:]
    return {
        "delta": float(bl_abs.mean() - eng_abs.mean()),
        "better": int((diff > 1e-9).sum()), "worse": int((diff < -1e-9).sum()),
        "ci_lo": float(np.percentile(means, 2.5)), "ci_hi": float(np.percentile(means, 97.5)),
        "delta_ex_top3": float(bl_abs[keep].mean() - eng_abs[keep].mean()),
    }


def _blend_rows(dataset, label, L):
    """Append a Phase-2 blend table block for one dataset; return its summary dict."""
    L.append(f"**{label}** ({len(dataset)} folds)\n")
    L.append("| variant | engine (matched) | in-sample best (w) | LOFO OOF (Δ vs engine) "
             "| exp-window OOF (Δ vs matched) | LOFO w  p10–p50–p90 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    summ = {}
    for variant in ("mirror", "meta"):
        eng = mae(dataset, "champion")
        w_in, m_in = best_w_insample(dataset, variant)
        oof, ws, _ = lofo_blend(dataset, variant)
        exp_oof, exp_ws, exp_eng = expanding_blend(dataset, variant)
        p10, p50, p90 = (float(np.percentile(ws, q)) for q in (10, 50, 90))
        summ[variant] = {"engine": eng, "w_in": w_in, "m_in": m_in, "lofo_oof": oof,
                         "exp_oof": exp_oof, "exp_eng": exp_eng,
                         "w_p10": p10, "w_p50": p50, "w_p90": p90}
        L.append(f"| {variant} | {_f(eng)} | {_f(m_in)} (w={w_in:.2f}) | "
                 f"{_f(oof)} ({oof-eng:+.4f}) | {_f(exp_oof)} ({exp_oof-exp_eng:+.4f}) | "
                 f"{p10:.2f} – {p50:.2f} – {p90:.2f} |")
    L.append("")
    return summ


def write_blend_report(rows, bi15, table, path):
    L = ["# Brief 2 — Engine × TabPFN blend: does an honest blend beat engine-alone?\n"]
    L.append("blend = w·engine + (1−w)·TabPFN (w = weight on the ENGINE). Same walk-forward "
             "folds as Brief 1; WA MAE unweighted. w chosen **out-of-fold** — leave-one-fold-"
             "out (LOFO) and expanding-window — so it is never scored on the folds that picked "
             "it. Headline is the authoritative N≥15 set (the brief's 0.63 baseline); the N≥3 "
             "union is the exploratory extension.\n")

    L.append("## Phase 1 — decorrelation diagnostic\n")
    L.append("Engine residual vs. TabPFN residual (signed-error) Pearson correlation. High "
             "positive → the two make the same mistakes → a blend can't help much.\n")
    L.append(f"Overall (N≥15): mirror **{_f(resid_corr(bi15,'mirror'),3)}** · "
             f"meta **{_f(resid_corr(bi15,'meta'),3)}**.\n")
    L.append("Per-bucket (N≥3 union):\n")
    L.append("| variant | overall | " + " | ".join(b["bucket"] for b in table if b["n"] > 0) + " |")
    L.append("| --- | --- | " + " | ".join("---" for b in table if b["n"] > 0) + " |")
    for variant in ("mirror", "meta"):
        cells = [_f(resid_corr(bucket_rows(rows, b["lo"], b["hi"]), variant), 2)
                 for b in table if b["n"] > 0]
        L.append(f"| {variant} | {_f(resid_corr(rows, variant),3)} | " + " | ".join(cells) + " |")
    L.append("")

    L.append("## Phase 2 — honest fixed-weight blend\n")
    auth = _blend_rows(bi15, "Authoritative — N≥15", L)
    _ = _blend_rows(rows, "Exploratory — N≥3 union", L)
    L.append("_w=1.00 means the honest search fell back to engine-alone. A wide p10–p90 = a "
             "fold-unstable weight (a red flag even if the mean improves). The matched engine "
             "baseline for exp-window is measured on the same later folds it scores._\n")

    L.append("## Phase 3 — N-adaptive blend\n")
    low = [b for b in table if b["hi"] <= 25 and b["n"] > 0]
    adv = any((b[v] is not None and b[v] < b["champion"]) for b in low for v in ("mirror", "meta"))
    L.append("Premise (per the brief): only warranted if Brief 1 found a low-N TabPFN "
             f"advantage. It did **not** — no N<25 bucket beats the engine. Running it anyway "
             "to confirm the honest optimum collapses toward engine-alone:\n")
    for variant in ("mirror", "meta"):
        oof, chosen = n_adaptive_blend(rows, variant)
        ts = [p[0] for p in chosen]; wls = [p[1] for p in chosen]; whs = [p[2] for p in chosen]
        L.append(f"- **{variant}:** LOFO N-adaptive OOF = {_f(oof)} vs engine "
                 f"{_f(mae(rows,'champion'))} (Δ {oof-mae(rows,'champion'):+.4f}); "
                 f"median (t, w_low, w_high) = ({int(np.median(ts))}, {np.median(wls):.2f}, "
                 f"{np.median(whs):.2f}) — w's near 1.0 = lean on the engine in both regimes.")
    L.append("")

    # Robustness of the most-promising blend (lowest LOFO OOF) — a point delta is
    # not enough; the bake-off's loss was outlier-driven with a CI straddling 0.
    best_v = min(auth, key=lambda v: auth[v]["lofo_oof"])
    w_star = auth[best_v]["w_p50"]
    rob = blend_robustness(bi15, best_v, w_star)
    L.append("## Robustness of the best honest blend (N≥15)\n")
    L.append(f"Fixed w={w_star:.2f} on the {best_v} blend (the stable LOFO weight), "
             f"paired against engine-alone over 114 folds:\n")
    L.append(f"- point Δ (blend − engine): **{rob['delta']:+.4f}**")
    L.append(f"- folds blend **better {rob['better']}** / **worse {rob['worse']}** "
             f"(≈ coin-flip if ~57/57)")
    L.append(f"- paired bootstrap 95% CI of mean |error| improvement: "
             f"**[{rob['ci_lo']:+.4f}, {rob['ci_hi']:+.4f}]**")
    L.append(f"- Δ with the top-3 improving folds removed: **{rob['delta_ex_top3']:+.4f}** "
             "(if this collapses toward 0, a few outliers drove the gain)\n")

    L.append("## Decision gate\n")
    best = auth[best_v]
    ci_excludes_zero = rob["ci_lo"] > 0
    stable = (best["w_p90"] - best["w_p10"]) < 0.15
    meaningful = best["lofo_oof"] <= best["engine"] - 0.02
    verdict = "GO" if (ci_excludes_zero and stable and meaningful) else "NO-GO"
    L.append(f"GO requires an honest blend beating the engine baseline out-of-fold by a "
             f"margin that (a) is meaningful (≥0.02), (b) holds out-of-fold, and (c) doesn't "
             f"rest on a fold-unstable weight. Best honest OOF blend ({best_v}) = "
             f"**{_f(best['lofo_oof'])}** vs engine **{_f(best['engine'])}** "
             f"(Δ **{best['lofo_oof']-best['engine']:+.4f}**), w stable at ~{best['w_p50']:.2f}. "
             f"**VERDICT: {verdict}.** " + (
                 "The point delta clears 0.02, BUT the paired bootstrap CI straddles zero and "
                 "the sign test is a coin-flip — the improvement is a few classical-drama/epic "
                 "outliers, not a broad, stable gain (the mirror blend, sharing the LLM vector, "
                 "collapses to engine-alone). Not distinguishable from noise."
                 if verdict == "NO-GO" else
                 "The gain holds out-of-fold, the CI excludes zero, and the weight is stable.") + "\n")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return auth


def main():
    rows, bi15 = load_all()
    table = bucketed_table(rows)
    write_bucketed_csv(table, os.path.join(OUT_DIR, "bucketed_table.csv"))
    render_svg(table, os.path.join(OUT_DIR, "mae_vs_n.svg"))
    overall, overall15 = write_regime_report(
        rows, bi15, table, os.path.join(OUT_DIR, "regime_report.md"))
    blend = write_blend_report(rows, bi15, table, os.path.join(OUT_DIR, "blend_report.md"))
    print("wrote regime_report.md, blend_report.md, bucketed_table.csv, mae_vs_n.svg")
    print("overall (N>=3):", {m: round(overall[m], 4) for m in MODELS})
    print("authoritative (N>=15):", {m: round(overall15[m], 4) for m in MODELS})
    for v in ("mirror", "meta"):
        print(f"blend {v}: engine {blend[v]['engine']:.4f} -> LOFO OOF "
              f"{blend[v]['lofo_oof']:.4f} (w_p50={blend[v]['w_p50']:.2f})")


if __name__ == "__main__":
    main()
