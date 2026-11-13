import type { CSSProperties } from "react";

/**
 * The tier fill (rankings WA circle, tier-list box) for a book at `index` of
 * `count` in its tier: --circle-hi-<spine> for the tier's best book, blending to
 * --circle-lo-<spine> for its lowest. Both are tuned in globals.css so white
 * text holds >= 3:1 across the whole range.
 */
export function tierBadgeFill(spineCls: string, index: number, count: number): string {
  const t = count > 1 ? Math.min(Math.max(index / (count - 1), 0), 1) : 0;
  const hiPct = Math.round(100 * (1 - t));
  return `color-mix(in srgb, var(--circle-hi-${spineCls}) ${hiPct}%, var(--circle-lo-${spineCls}))`;
}

/**
 * Position of each item inside its own group, best-first by score. Returns a
 * map key → { index, count }. Ties keep input order (stable sort).
 */
export function positionsInGroups<K>(
  items: { key: K; group: string; score: number }[],
): Map<K, { index: number; count: number }> {
  const groups = new Map<string, { key: K; score: number }[]>();
  for (const it of items) {
    const g = groups.get(it.group) ?? [];
    g.push({ key: it.key, score: it.score });
    groups.set(it.group, g);
  }
  const out = new Map<K, { index: number; count: number }>();
  for (const g of groups.values()) {
    const sorted = [...g].sort((a, b) => b.score - a.score);
    sorted.forEach((it, index) => out.set(it.key, { index, count: sorted.length }));
  }
  return out;
}

/* ── Server tiers → per-book lookup ────────────────────────────────────────
   The rankings list bands each book by the SAME tier the Tier List shows — the
   server's per-cohort percentile bands, never re-derived here — and shades it by
   its position inside that tier, ordered exactly as the Tier List orders it
   (primary score descending, stable). */

export type TierPos = { tier: string; index: number; count: number };
/** title → its tier and position within the tier, for one cohort. */
export type TierIndex = Record<string, TierPos>;

type TierBookLike = { title: string; tier: string; wa: number; total_average?: number | null };

export function buildTierIndex(
  books: TierBookLike[],
  tierOrder: string[],
  kind: "fiction" | "nonfiction",
): TierIndex {
  const score = (b: TierBookLike) => (kind === "nonfiction" ? (b.total_average ?? 0) : b.wa);
  const out: TierIndex = {};
  for (const t of tierOrder) {
    const inTier = books.filter((b) => b.tier === t).sort((a, b) => score(b) - score(a));
    inTier.forEach((b, index) => {
      out[b.title] = { tier: t, index, count: inTier.length };
    });
  }
  return out;
}

const SPINE_CLASS: Record<string, string> = {
  "S+": "spine-sp", S: "spine-s", A: "spine-a", B: "spine-b", C: "spine-c", D: "spine-d", F: "spine-f",
};

/** The `.spine-*` class for a tier label (undefined for an unknown label). */
export function tierSpineClass(tier: string): string | undefined {
  return SPINE_CLASS[tier];
}

/* ── Tier-coloured surfaces for a book's standing ──────────────────────────
   Shared by Add a Book and the Rankings score editor. `standing` carries the
   server's tier + slot (preview or saved); without a tier these fall back to
   the plain sage look. */
type TierStandingLike = { tier?: string; tier_index?: number; tier_count?: number } | null | undefined;

/** A WA circle in the book's tier fill with white numbers (undefined → sage). */
export function tierCircleStyle(s: TierStandingLike): CSSProperties | undefined {
  const cls = s?.tier ? tierSpineClass(s.tier) : undefined;
  if (!s || !cls) return undefined;
  return {
    background: tierBadgeFill(cls, s.tier_index ?? 0, s.tier_count ?? 1),
    color: "var(--color-on-accent)",
    fontWeight: 800,
    textShadow: "0 1px 2px rgba(0, 0, 0, 0.3)",
    boxShadow: "0 1px 3px rgba(0, 0, 0, 0.25)",
  };
}

/** A success notice in the book's tier fill with white semibold text. */
export function tierNoticeStyle(s: TierStandingLike): CSSProperties {
  const cls = s?.tier ? tierSpineClass(s.tier) : undefined;
  if (!s || !cls) {
    return { background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" };
  }
  return {
    background: tierBadgeFill(cls, s.tier_index ?? 0, s.tier_count ?? 1),
    color: "var(--color-on-accent)",
    fontWeight: 600,
    textShadow: "0 1px 2px rgba(0, 0, 0, 0.3)",
    border: "1px solid transparent",
  };
}

/** A solid action button in the book's tier fill (its tier's best-book shade,
 *  so it reads as the strongest colour on the page) with white text; sage when
 *  there's no tier yet. */
export function tierButtonStyle(s: TierStandingLike): CSSProperties {
  const cls = s?.tier ? tierSpineClass(s.tier) : undefined;
  if (!cls) return { background: "var(--color-sage)", color: "var(--color-on-accent)" };
  return {
    background: `var(--circle-hi-${cls})`,
    color: "var(--color-on-accent)",
    textShadow: "0 1px 2px rgba(0, 0, 0, 0.3)",
  };
}
