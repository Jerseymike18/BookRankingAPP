#!/usr/bin/env python3
"""
branch_a_eval.py — Phase 3 Branch A: walk-forward evaluation of text-embedding
candidates for component/WA estimation, vs the Phase-1 honest baseline.

Protocol is IDENTICAL to walkforward.py: DB read_seq order, past-only pools,
time/author/series splits, burn-in 15. Everything trainable is fit INSIDE each
fold's pool (StandardScaler, PCA, RidgeCV, kNN) — no corpus-wide fit, so no leak.

Candidates (each predicts the held-out book's WA from its pool):
  baseline           the honest engine (read from the committed walk-forward folds)
  ridge_llm          RidgeCV(actual WA ~ raw LLM 14-vector)                 [control]
  ridge_emb          RidgeCV(actual WA ~ in-fold PCA of the text embedding) [text only]
  ridge_llm_emb      RidgeCV(actual WA ~ LLM 14 + PCA embedding)            [features]
  ridge_llm_emb_time ... + read_seq (the 2.3 slow-time term)
  knn_emb            cosine-similarity-weighted kNN on actual WA (the CF kernel)
  blend_base_knn     0.5*baseline + 0.5*knn_emb

Reports WA MAE + Spearman + Kendall per candidate per split, vs baseline.
Deterministic. Read-only (writes only the markdown report).
"""
import json
import os
import sys

import numpy as np
from scipy import stats
from sklearn.linear_model import RidgeCV
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_BACKEND", "sqlite")

import walkforward as wf
import db_loader
import research_predict as rp

LIVE = wf.LIVE
SPLITS = wf.SPLIT_MODES
BURN = wf.BURN_IN_DEFAULT
ALPHAS = [0.1, 1.0, 10.0, 100.0]
PCA_MAX = 12
KNN_K = 10
OUT = os.path.join(ROOT, "validation", "branch_a_results.md")
BASELINE_FOLDS = {
    "time": os.path.join(ROOT, "validation", "walkforward_folds.jsonl"),
    "author": os.path.join(ROOT, "validation", "splits", "author", "walkforward_folds.jsonl"),
    "series": os.path.join(ROOT, "validation", "splits", "series", "walkforward_folds.jsonl"),
}
CANDS = ["baseline", "ridge_llm", "ridge_emb", "ridge_llm_emb",
         "ridge_llm_emb_time", "knn_emb", "blend_base_knn"]


def _load_baseline(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if not r.get("skip"):
            out[r["title"]] = r["variants"]["honest"]["wa"]
    return out


def _ridge(X, y, xt):
    """RidgeCV (in-pool LOO over ALPHAS) on standardized features; predict xt.
    Clipped to the valid WA domain [0,10]."""
    sc = StandardScaler().fit(X)
    model = RidgeCV(alphas=ALPHAS).fit(sc.transform(X), y)
    p = float(model.predict(sc.transform(np.asarray(xt).reshape(1, -1)))[0])
    return min(10.0, max(0.0, p))


def _knn(Epool, y, e_t, k):
    sims = Epool @ e_t                      # cosine (all L2-normalised)
    k = min(k, len(y))
    top = np.argsort(-sims)[:k]
    w = np.clip(sims[top], 0.0, None)
    if w.sum() <= 1e-9:
        return float(np.mean(y[top]))
    return float(np.clip((w * y[top]).sum() / w.sum(), 0.0, 10.0))


def _metrics(pred, actual):
    pairs = [(p, a) for p, a in zip(pred, actual) if p is not None]
    if len(pairs) < 3:
        return None
    P = [p for p, _ in pairs]
    A = [a for _, a in pairs]
    mae = sum(abs(p - a) for p, a in pairs) / len(pairs)
    return {"mae": mae, "rho": float(stats.spearmanr(P, A)[0]),
            "tau": float(stats.kendalltau(P, A)[0]), "n": len(pairs)}


def main():
    books, gw, gcw = db_loader.load_from_db()
    cache = rp.load_cache()
    wa = {r["Book"]: float(r["WA"]) for _, r in books.iterrows()}

    emb = np.load(os.path.join(ROOT, "validation", "book_embeddings.npy"))
    etitles = json.load(open(os.path.join(ROOT, "validation", "book_embeddings_titles.json")))
    eidx = {t: i for i, t in enumerate(etitles)}

    order, _ = wf.build_order(books)
    rseq = {e["title"]: (e["read_seq"] or 0) for e in order}

    def llm_ok(t):
        return (t in cache and isinstance(cache[t].get("scores"), dict)
                and all(c in cache[t]["scores"] for c in LIVE) and t in eidx and t in wa)

    def llm_vec(t):
        return np.array([float(cache[t]["scores"][c]) for c in LIVE])

    results = {}          # split -> {cand -> metrics}
    dump = {}             # split -> {actual:[...], titles:[...], cand:[...]}
    for split in SPLITS:
        base = _load_baseline(BASELINE_FOLDS[split])
        preds = {c: [] for c in CANDS}
        actual = []
        evt = []
        for idx, e in enumerate(order):
            t = e["title"]
            if not llm_ok(t):
                continue
            pool = [pt for pt in wf._pool_titles(order, idx, split) if llm_ok(pt)]
            if len(pool) < BURN:
                continue

            y = np.array([wa[pt] for pt in pool])
            Xllm = np.vstack([llm_vec(pt) for pt in pool])
            Ep = emb[[eidx[pt] for pt in pool]]
            rs = np.array([rseq[pt] for pt in pool], dtype=float)
            xllm_t, e_t, rs_t = llm_vec(t), emb[eidx[t]], float(rseq[t])

            k = min(PCA_MAX, max(2, len(pool) - 2))
            pca = PCA(n_components=k, random_state=0).fit(Ep)
            Ep_r = pca.transform(Ep)
            e_t_r = pca.transform(e_t.reshape(1, -1))[0]

            b = base.get(t)
            kn = _knn(Ep, y, e_t, KNN_K)
            preds["baseline"].append(b)
            preds["ridge_llm"].append(_ridge(Xllm, y, xllm_t))
            preds["ridge_emb"].append(_ridge(Ep_r, y, e_t_r))
            preds["ridge_llm_emb"].append(_ridge(np.hstack([Xllm, Ep_r]), y,
                                                 np.hstack([xllm_t, e_t_r])))
            preds["ridge_llm_emb_time"].append(_ridge(
                np.hstack([Xllm, Ep_r, rs[:, None]]), y,
                np.hstack([xllm_t, e_t_r, [rs_t]])))
            preds["knn_emb"].append(kn)
            preds["blend_base_knn"].append(0.5 * b + 0.5 * kn if b is not None else None)
            actual.append(wa[t])
            evt.append(t)
        results[split] = {c: _metrics(preds[c], actual) for c in CANDS}
        dump[split] = {"actual": actual, "titles": evt, **{c: preds[c] for c in CANDS}}
    json.dump(dump, open(os.path.join(ROOT, "validation", "branch_a_preds.json"), "w"),
              indent=0)

    # ---- report ----
    L = ["# Branch A — text-embedding candidates (Phase 3)\n"]
    L.append("Walk-forward (read_seq order, past-only pools, burn-in 15). All models fit "
             "in-fold (StandardScaler / PCA≤12 / RidgeCV / kNN k=10) — no leakage. Text = "
             "MiniLM(384-d) of a spoiler-free premise/themes description. **Lower MAE + "
             "higher ρ/τ is better.**\n")
    for split in SPLITS:
        base_mae = (results[split]["baseline"] or {}).get("mae")
        L.append(f"## Split: {split}\n")
        L.append("| candidate | WA MAE | Δ vs base | Spearman ρ | Kendall τ | n |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        for c in CANDS:
            m = results[split][c]
            if not m:
                L.append(f"| {c} | - | - | - | - | - |")
                continue
            dv = "" if base_mae is None else f"{m['mae'] - base_mae:+.3f}"
            star = " ⭐" if (base_mae is not None and m["mae"] < base_mae - 1e-9 and c != "baseline") else ""
            L.append(f"| {c}{star} | {m['mae']:.3f} | {dv} | {m['rho']:.3f} | {m['tau']:.3f} | {m['n']} |")
        L.append("")
    md = "\n".join(L) + "\n"
    open(OUT, "w", encoding="utf-8").write(md)
    print(md)
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
