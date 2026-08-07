"""
score_anchors.py — per-user RATING-SCALE anchors (the prose→number mapping)
===========================================================================
WHAT THIS IS
------------
Grounded research asks the LLM to read what reader communities actually say
about a book and convert that SENTIMENT into 0-10 component scores. The
conversion table lives in the research prompt
(``reresearch_and_measure.ANCHORS``) and is one reader's judgement call:

    "best in genre / blew me away"        -> 9.0-9.5
    "one of my favorites / would re-read" -> 8.5-9.0
    ...
    "bad / DNF"                           -> <=4.0

Two readers can agree completely about a book and still disagree about what
number "really strong" deserves. This module lets each tenant set their OWN
numbers for those seven sentiment bands, and applies them to the LLM's raw
scores BEFORE the engine's corrections run.

HOW IT IS APPLIED (and why it is not a per-user prompt)
-------------------------------------------------------
The prompt stays canonical for everyone and the research cache stays global —
one book is researched once, ever, for all users. Personalisation is a
deterministic MONOTONE REMAP of the raw 0-10 vector afterwards: the canonical
band centre is mapped onto the reader's centre, piecewise-linearly, with the
top segment's slope extrapolated above the highest anchor and the result
clamped to the 0-10 component domain.

That is exactly what re-anchoring means (the same review prose now lands on a
different number) and it buys three things a per-user prompt cannot:

  * zero extra LLM spend and no cache fragmentation (raw vectors are shared);
  * instant effect — changing an anchor re-prices books already researched,
    with no re-research;
  * exact reversibility — the default anchors produce the identity map, so
    every existing prediction is byte-identical (guarded by test_engine.py
    and test_score_anchors.py).

WHERE IT SITS IN THE PIPELINE
-----------------------------
    grounded research (canonical prompt, shared cache)
      -> ANCHOR REMAP (this module, per user)          <-- the reader's scale
      -> correlation smoothing + author/genre correction (unchanged engine)
      -> WA roll-up

Only the TARGET book's raw scores are remapped. The correction ladder's
training pairs (your rated books x their cached raw LLM scores) stay canonical
on purpose: the correction is calibrated against the model's raw biases, and
remapping both sides would let the refit silently undo the reader's choice.

READ-ONLY: this module never writes. Storage lives in ``score_anchors``
(db_write.set_score_anchors / reset_score_anchors); the band keys and the
write-side validation live there too.
"""

import db_write  # storage (band keys, reads, the validated write gate)

# The seven sentiment bands of the canonical research prompt, in ascending
# order. `label` is the prompt's own wording, `hint` the canonical range it
# states, and `default` the centre this module maps onto (the midpoint of that
# range; the open-ended top/bottom bands use the midpoint of their effective
# span). Source of truth for the wording: reresearch_and_measure.ANCHORS —
# test_score_anchors.py asserts every label below still appears there, so a
# prompt edit can't silently desync the editor from what the LLM is told.
BANDS = [
    {"key": "bad",      "label": "bad / DNF",                            "hint": "4.0 and below", "default": 3.5},
    {"key": "weak",     "label": "disappointing / weak",                 "hint": "5.0 – 6.0",     "default": 5.5},
    {"key": "fine",     "label": "fine / didn't grab me",                "hint": "6.0 – 7.0",     "default": 6.5},
    {"key": "good",     "label": "good, enjoyed it",                     "hint": "7.0 – 8.0",     "default": 7.5},
    {"key": "strong",   "label": "really strong / recommend it",         "hint": "8.0 – 8.5",     "default": 8.25},
    {"key": "favorite", "label": "one of my favorites / would re-read",  "hint": "8.5 – 9.0",     "default": 8.75},
    {"key": "best",     "label": "best in genre / blew me away",         "hint": "9.0 – 9.5",     "default": 9.25},
]

KEYS = [b["key"] for b in BANDS]
DEFAULTS = {b["key"]: b["default"] for b in BANDS}

# The editor and the write side must agree on the band set (db_write owns the
# keys so it can validate without importing this module).
assert KEYS == list(db_write.SCORE_ANCHOR_BANDS), "score-anchor band sets disagree"

TOL = 1e-9


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------
def load_anchors(user_id=None):
    """This tenant's anchor centres as {band_key: value}, defaults filled in for
    every band they haven't set. Never raises on a missing table/row — a reader
    with no stored anchors simply gets DEFAULTS (the identity remap)."""
    values = dict(DEFAULTS)
    try:
        stored = db_write.get_score_anchors(user_id=user_id)
    except Exception:
        return values
    for k, v in (stored or {}).items():
        if k in values and v is not None:
            values[k] = float(v)
    return values


def is_default(values):
    """True when `values` is (numerically) the canonical anchor table — i.e. the
    remap is the identity and every downstream number is untouched."""
    if not values:
        return True
    return all(abs(float(values.get(k, DEFAULTS[k])) - DEFAULTS[k]) <= TOL
               for k in KEYS)


def effective_anchors(user_id=None):
    """Display payload for the anchor editor: one row per band (key, label,
    canonical hint, default, the reader's value) plus a `customized` flag."""
    values = load_anchors(user_id)
    return {
        "bands": [
            {"key": b["key"], "label": b["label"], "hint": b["hint"],
             "default": b["default"], "value": round(float(values[b["key"]]), 4)}
            for b in BANDS
        ],
        "customized": not is_default(values),
    }


# ---------------------------------------------------------------------------
# The remap
# ---------------------------------------------------------------------------
def _knots(values):
    """Ascending [(canonical_x, user_y)] control points: the origin (0,0) plus
    one point per band. Assumes `values` is validated (nondecreasing, 0-10);
    db_write.set_score_anchors is the gate."""
    pts = [(0.0, 0.0)]
    pts += [(float(b["default"]), float(values.get(b["key"], b["default"])))
            for b in BANDS]
    return pts


def remap_value(x, values):
    """Map one raw 0-10 score onto the reader's scale.

    Piecewise-linear through the anchor knots; above the top anchor the last
    segment's slope is extrapolated (so a 9.8 stays meaningfully above a 9.25
    instead of being squeezed against a pinned ceiling), and the result is
    clamped to the definitional 0-10 component domain. Monotone by construction
    — a reader's anchors can move scores, never reorder them."""
    pts = _knots(values)
    x = float(x)
    if x <= pts[0][0]:
        return max(0.0, min(10.0, pts[0][1]))
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            span = x1 - x0
            t = (x - x0) / span if span > TOL else 1.0
            return max(0.0, min(10.0, y0 + t * (y1 - y0)))
    # Above the highest anchor: extrapolate the top segment's slope.
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    span = x1 - x0
    slope = (y1 - y0) / span if span > TOL else 1.0
    return max(0.0, min(10.0, y1 + slope * (x - x1)))


def remap_scores(scores, values):
    """Apply the reader's anchors to a {component: raw_score} vector.

    Returns a NEW dict; non-numeric / missing values pass through untouched.
    Returns the input unchanged (same object) when `values` is None or the
    canonical table, so every default-anchor caller is byte-identical."""
    if not scores or values is None or is_default(values):
        return scores
    out = {}
    for c, v in scores.items():
        try:
            out[c] = remap_value(float(v), values)
        except (TypeError, ValueError):
            out[c] = v
    return out


def remap_for_user(scores, user_id=None):
    """Convenience: load this tenant's anchors and remap `scores` with them.
    Best-effort — any storage failure degrades to the canonical scores rather
    than failing a prediction."""
    try:
        return remap_scores(scores, load_anchors(user_id))
    except Exception:
        return scores
