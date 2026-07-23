"""
challenger_tabpfn.py
====================
The TabPFN challenger predictor for the walk-forward bake-off.

WHAT IT DOES
------------
A single-book WA point predictor built on TabPFN v2 (Prior-Labs, Apache-2.0,
synthetic-pretrained, no token). TabPFN is an *in-context* learner, which maps
onto walk-forward with zero fitting:

    context = causal feature rows for ALL books read before the target
              + their observed WAs
    query   = the target book's causal feature row
    predict = one forward pass  (no gradient training, no per-fold retraining)

So each fold is a fresh `fit(context) -> predict(query)`; "fit" merely loads the
context rows into the transformer, it does not train weights.

INTERFACE (mirrors the Phase-0 swap point: `(book@t, past-only pool) -> WA`)
---------------------------------------------------------------------------
  * predict_one(context_X, context_y, query_X)   -> float
  * run_walkforward(feature_matrix, y_all, eval_indices, ...) -> {i: {...}}
    walks the sequence, using the strictly-earlier prefix as context for each
    evaluated index. This is the entry the bake-off harness calls.

DETERMINISM
-----------
The gate is only honest if the challenger is reproducible. We seed numpy + torch
AND pass TabPFN's own `random_state` before every fit, so repeated runs are
byte-identical (verified: two full passes hash-identical; see the harness's
--check-determinism). CPU only — this N (≈110 context rows, 8 features) is far
below TabPFN's limits and needs no GPU.

SCOPE
-----
Point predictions only. Interval logic lives elsewhere (Phase 4 reuses the
engine's conformal wrapper over these point predictions; TabPFN's native
quantiles are captured for curiosity but are NOT a served band).
"""

import numpy as np

SEED = 42
DEVICE = "cpu"
# TabPFN v2 default ensemble size; fixed here so the config is explicit/pinned.
N_ESTIMATORS = 8


def _seed_all(seed):
    """Seed every RNG TabPFN can touch, so a fit/predict is reproducible."""
    import torch
    np.random.seed(seed)
    torch.manual_seed(seed)


class TabPFNChallenger:
    """Thin, seeded wrapper around TabPFNRegressor. Construct once, reuse across
    folds (each fold re-`fit`s its own context; we re-seed before every fit so
    call order cannot affect a result)."""

    def __init__(self, seed=SEED, device=DEVICE, n_estimators=N_ESTIMATORS):
        self.seed = seed
        self.device = device
        self.n_estimators = n_estimators
        self._reg = None

    def _regressor(self):
        if self._reg is None:
            from tabpfn import TabPFNRegressor
            self._reg = TabPFNRegressor(
                device=self.device, random_state=self.seed,
                n_estimators=self.n_estimators)
        return self._reg

    def predict_one(self, context_X, context_y, query_X, want_quantiles=False,
                    quantiles=(0.1, 0.9)):
        """Predict one book's WA from its past-only context.

        context_X : (n, F) float array (NaN = missing; TabPFN handles it)
        context_y : (n,) float array of the context books' actual WAs
        query_X   : (F,) or (1, F) float array for the held-out book
        Returns float, or (float, {q: value}) when want_quantiles.
        """
        import warnings
        _seed_all(self.seed)
        reg = self._regressor()
        cx = np.asarray(context_X, dtype=float)
        cy = np.asarray(context_y, dtype=float)
        qx = np.asarray(query_X, dtype=float).reshape(1, -1)
        # Early folds have whole feature columns that are all-NaN by design (e.g.
        # author_prior_std before any author has 2 prior reads); TabPFN's internal
        # nan-robust scaling warns "All-NaN slice" and then imputes. Benign — scope
        # it out so the run's output stays readable (nothing about the result changes).
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="All-NaN slice encountered",
                                    category=RuntimeWarning)
            reg.fit(cx, cy)
            point = float(np.asarray(reg.predict(qx), dtype=float)[0])
        if not want_quantiles:
            return point
        qs = list(quantiles)
        qout = reg.predict(qx, output_type="quantiles", quantiles=qs)
        qmap = {q: float(np.asarray(arr, dtype=float)[0]) for q, arr in zip(qs, qout)}
        return point, qmap

    def run_walkforward(self, feature_matrix, y_all, eval_indices,
                        want_quantiles=False, quantiles=(0.1, 0.9)):
        """Walk the sequence and predict every evaluated index.

        feature_matrix : (N, F) causal features in walk-forward position order
                         (row i == the book at position i+1, its features built
                         from rows 0..i-1).
        y_all          : (N,) actual WA in the same order.
        eval_indices   : iterable of 0-based indices to predict. For index i the
                         context is the strictly-earlier prefix rows[0:i], y[0:i].
        Returns {i: {"pred": float, "quantiles": {q: v} | None}}.
        """
        X = np.asarray(feature_matrix, dtype=float)
        y = np.asarray(y_all, dtype=float)
        out = {}
        for i in sorted(eval_indices):
            if i <= 0:
                raise ValueError(f"eval index {i} has an empty past-only context")
            cx, cy, qx = X[:i], y[:i], X[i]
            if want_quantiles:
                point, qmap = self.predict_one(cx, cy, qx, want_quantiles=True,
                                               quantiles=quantiles)
                out[i] = {"pred": point, "quantiles": qmap}
            else:
                out[i] = {"pred": self.predict_one(cx, cy, qx), "quantiles": None}
        return out
