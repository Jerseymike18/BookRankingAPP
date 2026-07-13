"""
user_weights.py — per-user genre/component weight OVERRIDES (read-only merge)
=============================================================================
The global weight tables are the shared cold-start prior. A tenant may tailor
the weighting to their own taste; those edits are stored SPARSELY in override
tables (LONG format) by db_write, and overlaid on the globals at load time. This
module is the READ side of that layer, for BOTH tracks:

  * ``load_overrides(user_id)`` / ``load_overrides_nf(user_id)`` — the
    (gw_over, gcw_over) pair the engine feed passes to the loader's
    ``weight_overrides=`` param. Shaped exactly like that loader's gw / gcw so it
    just dict-merges — no math here.
  * ``effective_weights(user_id)`` / ``effective_weights_nf(user_id)`` — the
    display payload for the weights editor: global defaults overlaid with the
    user's overrides, plus per-group ``customized`` flags.

Fiction and nonfiction differ only in table names, category set, and gw-key
casing (fiction gw is keyed "Story"…; nonfiction gw is keyed lowercase
"quality"…). Those differences live in the SPEC dicts below; the logic is shared.

READ-ONLY: never writes. All writes go through db_write.set_*_weights /
reset_*_weights.
"""

import db_backend
import db_write  # constants (FICTION_COMPONENTS / NONFICTION_COMPONENTS) + ensures tables exist

DB = "books.db"

# Per-track config. `gw_cols` is (Capitalized category, global-table column) in the
# order the editor renders; `gw_key` maps a category to the key the LOADER's gw
# dict uses (identity for fiction, lowercase for nonfiction).
FICTION_SPEC = {
    "genre_over": "genre_weight_overrides",
    "gcomp_over": "gcomp_weight_overrides",
    "genre_tbl": "genre_weights",
    "gcomp_tbl": "gcomp_weights",
    "gw_cols": [("Story", "story"), ("Character", "character"), ("Theme", "theme"),
                ("Aesthetics", "aesthetics"), ("Worldbuilding", "worldbuilding")],
    "comp_order": db_write.FICTION_COMPONENTS,
    "gw_key": lambda c: c,
}
NONFICTION_SPEC = {
    "genre_over": "nonfiction_genre_weight_overrides",
    "gcomp_over": "nonfiction_gcomp_weight_overrides",
    "genre_tbl": "nonfiction_genre_weights",
    "gcomp_tbl": "nonfiction_gcomp_weights",
    "gw_cols": [("Quality", "quality"), ("Aesthetics", "aesthetics"), ("Theme", "theme")],
    "comp_order": db_write.NONFICTION_COMPONENTS,
    "gw_key": lambda c: c.lower(),
}


def _round(d):
    return {k: round(float(v), 6) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Generic core (spec-driven)
# ---------------------------------------------------------------------------
def _load_overrides(spec, user_id=None, path=DB):
    """Return (gw_over, gcw_over) for one tenant, shaped like the track's loader:
       gw_over[genre][<gw-key>]              = weight   (gw-key per spec.gw_key)
       gcw_over[genre][category][component]  = weight   (category capitalized)
    Only overridden groups are present; empty dicts when the user has none."""
    con = db_backend.connect(path)
    uid = user_id or db_backend.DEFAULT_USER_ID
    gw_key = spec["gw_key"]
    try:
        gw_over: dict = {}
        for genre, cat, w in con.execute(
                f"SELECT genre, category, weight FROM {spec['genre_over']} "
                f"WHERE user_id=?", (uid,)):
            gw_over.setdefault(genre, {})[gw_key(cat)] = w
        gcw_over: dict = {}
        for genre, cat, comp, w in con.execute(
                f"SELECT genre, category, component, weight FROM {spec['gcomp_over']} "
                f"WHERE user_id=?", (uid,)):
            gcw_over.setdefault(genre, {}).setdefault(cat, {})[comp] = w
    finally:
        con.close()
    return gw_over, gcw_over


def _effective_weights(spec, user_id=None, path=DB):
    """Global defaults overlaid with the user's overrides, plus ``customized``
    flags — the payload the weights editor renders (categories capitalized)."""
    categories = [cap for cap, _ in spec["gw_cols"]]
    comp_index = {c: i for i, c in enumerate(spec["comp_order"])}
    col_list = ",".join(col for _, col in spec["gw_cols"])
    con = db_backend.connect(path)
    uid = user_id or db_backend.DEFAULT_USER_ID
    try:
        g_cat: dict = {}
        for row in con.execute(f"SELECT genre,{col_list} FROM {spec['genre_tbl']}"):
            g_cat[row[0]] = {cap: row[i + 1] for i, (cap, _) in enumerate(spec["gw_cols"])}
        g_comp: dict = {}
        for genre, cat, comp, w in con.execute(
                f"SELECT genre, category, component, weight FROM {spec['gcomp_tbl']}"):
            g_comp.setdefault(genre, {}).setdefault(cat, {})[comp] = w
        gw_over, gcw_over = {}, {}
        for genre, cat, w in con.execute(
                f"SELECT genre, category, weight FROM {spec['genre_over']} "
                f"WHERE user_id=?", (uid,)):
            gw_over.setdefault(genre, {})[cat] = w
        for genre, cat, comp, w in con.execute(
                f"SELECT genre, category, component, weight FROM {spec['gcomp_over']} "
                f"WHERE user_id=?", (uid,)):
            gcw_over.setdefault(genre, {}).setdefault(cat, {})[comp] = w
    finally:
        con.close()

    # Genres = the global set PLUS any the user defined privately (override-only).
    all_genres = sorted(set(g_cat) | set(gw_over) | set(gcw_over))
    genres_out = []
    for genre in all_genres:
        is_custom = genre not in g_cat          # override-only → a private genre
        default_cats = g_cat.get(genre, {})     # {} for a custom genre
        over_cats = gw_over.get(genre, {})
        eff_cats = {c: over_cats.get(c, default_cats.get(c, 0.0)) for c in categories}

        # Component structure = the global gcomp for this genre, each category
        # group replaced wholesale by an override group when present. For a custom
        # genre g_comp is empty, so the structure comes entirely from the overrides.
        base_comp = {cat: dict(comps) for cat, comps in g_comp.get(genre, {}).items()}
        for cat, comps in gcw_over.get(genre, {}).items():
            base_comp[cat] = dict(comps)

        cats_out = []
        for cat in categories:
            comps = base_comp.get(cat)
            if not comps:
                continue  # category with no components for this genre — nothing to split
            ordered = sorted(comps, key=lambda c: comp_index.get(c, 999))
            over_comp = gcw_over.get(genre, {}).get(cat, {})
            default_comp = g_comp.get(genre, {}).get(cat, {})  # {} for a custom genre
            cats_out.append({
                "category": cat,
                "components": ordered,
                "effective": _round({c: comps[c] for c in ordered}),
                # a custom genre has no separate default → show effective as default
                "default": _round({c: default_comp.get(c, comps[c]) for c in ordered}),
                "customized": bool(over_comp),
            })

        genres_out.append({
            "genre": genre,
            "custom": is_custom,
            "category_weights": {
                "effective": _round(eff_cats),
                "default": _round({c: default_cats.get(c, eff_cats[c]) for c in categories}),
                "customized": bool(over_cats),
            },
            "categories": cats_out,
        })

    return {"categories": list(categories), "genres": genres_out}


# ---------------------------------------------------------------------------
# Public per-track wrappers
# ---------------------------------------------------------------------------
def load_overrides(user_id=None, path=DB):
    """Fiction (gw_over, gcw_over)."""
    return _load_overrides(FICTION_SPEC, user_id, path)


def effective_weights(user_id=None, path=DB):
    """Fiction weights-editor payload."""
    return _effective_weights(FICTION_SPEC, user_id, path)


def load_overrides_nf(user_id=None, path=DB):
    """Nonfiction (gw_over, gcw_over) — gw keyed lowercase to match the engine."""
    return _load_overrides(NONFICTION_SPEC, user_id, path)


def effective_weights_nf(user_id=None, path=DB):
    """Nonfiction weights-editor payload."""
    return _effective_weights(NONFICTION_SPEC, user_id, path)
