/**
 * A tier-coloured WA badge's fill for a book at `index` of `count` in its tier:
 * --badge-hi-<spine> for the tier's best book, blending to --badge-lo-<spine>
 * for its lowest (globals.css picks the fade direction per theme for contrast).
 */
export function tierBadgeFill(spineCls: string, index: number, count: number): string {
  const t = count > 1 ? Math.min(Math.max(index / (count - 1), 0), 1) : 0;
  const hiPct = Math.round(100 * (1 - t));
  return `color-mix(in srgb, var(--badge-hi-${spineCls}) ${hiPct}%, var(--badge-lo-${spineCls}))`;
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
