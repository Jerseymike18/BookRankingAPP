"""
intervals.py
============
Conformal prediction intervals from leave-one-out residuals, bucketed by data
density. This module is ADDITIVE to the engine: it never changes a point
prediction. It maps a prediction's density (how many same-author analogs the
library holds) to an empirical 80% interval half-width, using a residual table
precomputed OFFLINE by `python3 validate_engine.py --write-residuals`.

Single source of truth for the density-bucket definition, imported by BOTH the
LOO harness (validate_engine.py) and the serving path (backend/main.py) so the
two can never drift. Drift between how residuals are bucketed and how a live
prediction is bucketed would silently miscover.

Deliberately light: stdlib only (json, hashlib, os). No pandas / numpy / engine
imports, so the serving path stays cheap and this module cannot form an import
cycle with validate_engine (which imports it).
"""

import hashlib
import json
import os

# ---------------------------------------------------------------------------
# Canonical density buckets
# ---------------------------------------------------------------------------
# n is the number of SAME-AUTHOR analog books available to the prediction:
#   * at prediction time -> books by this author already in the library
#   * during LOO         -> books by this author in the training fold (n-1 for
#     an author with n books total, since the held-out book is removed)
# Both are "analogs available", so ONE threshold set serves both. These match
# the shrinkage-work buckets exactly (author is the engine's innermost tier).
BUCKET_ORDER = ["cluster n>=6", "cluster 2<=n<6", "author-only n=1",
                "genre-only n=0"]

# Human-facing labels for the reliability panel.
BUCKET_LABELS = {
    "cluster n>=6": "author-rich",
    "cluster 2<=n<6": "some author data",
    "author-only n=1": "single author book",
    "genre-only n=0": "genre only",
}

# Nearest-neighbour pooling partner for a THIN bucket (< MIN_BUCKET_N residuals).
# Only the two thin buckets get partners; the large author buckets never pool.
POOL_PARTNER = {
    "author-only n=1": "cluster 2<=n<6",   # borrow the next-richer author tier
    "genre-only n=0": "__global__",        # borrow the whole residual set
}
MIN_BUCKET_N = 20            # below this many residuals a bucket borrows a neighbour
COVERAGE_TARGET = 0.80       # an 80% interval -> the 80th percentile of |residual|


def density_bucket(n_author):
    """Map a same-author analog count to a density bucket. THE canonical
    definition — used identically by the LOO harness and the serving path."""
    if n_author >= 6:
        return "cluster n>=6"
    if n_author >= 2:
        return "cluster 2<=n<6"
    if n_author == 1:
        return "author-only n=1"
    return "genre-only n=0"


def bucket_label(bucket):
    return BUCKET_LABELS.get(bucket, bucket)


def should_pool(bucket, n_own):
    """A bucket pools iff it has a defined neighbour AND fewer than MIN_BUCKET_N
    residuals of its own. Large author buckets have no partner and never pool."""
    return bucket in POOL_PARTNER and n_own < MIN_BUCKET_N


# ---------------------------------------------------------------------------
# Engine staleness
# ---------------------------------------------------------------------------
# The residual table is only valid for the engine that produced it. We hash the
# source of the two files that determine the residuals; if either changes, a
# previously written residuals.json is stale. Content-hashing (not the git SHA)
# so uncommitted edits are caught too.
_ENGINE_FILES = ("predict_engine.py", "validate_engine.py")


def engine_hash(root=None):
    root = root or os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in _ENGINE_FILES:
        try:
            with open(os.path.join(root, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"\0MISSING\0")
    return "sha256:" + h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Residual table I/O + interval lookup (serving side)
# ---------------------------------------------------------------------------
def load_residuals(path):
    """Load a residuals.json artifact, or None if it is missing / unreadable.
    Never raises: a missing table simply means 'serve no intervals'."""
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def interval_for(table, n_author, current_hash=None):
    """Return the interval descriptor for a new prediction's density, or None if
    the table is absent / has no half-width for that bucket (caller then omits
    the interval fields entirely — never invent a width). Does NOT centre the
    interval: the caller adds/subtracts half_width from the point prediction.

    Returns { bucket, bucket_label, half_width, pooled, calibrated_at, stale }.
    """
    if not table:
        return None
    bucket = density_bucket(n_author)
    binfo = (table.get("buckets") or {}).get(bucket)
    if not binfo or binfo.get("half_width") is None:
        return None
    stale = bool(current_hash and table.get("engine_hash")
                 and current_hash != table.get("engine_hash"))
    return {
        "bucket": bucket,
        "bucket_label": bucket_label(bucket),
        "half_width": float(binfo["half_width"]),
        "pooled": bool(binfo.get("pooled", False)),
        "calibrated_at": table.get("generated_at"),
        "stale": stale,
    }
