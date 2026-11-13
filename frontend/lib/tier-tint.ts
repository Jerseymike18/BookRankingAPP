/**
 * In-tier shading. A book's surface tints in its tier's own hue — deepest for
 * the tier's best book, fading toward the surface for its lowest. Presentation
 * only: the position comes from an existing best-first order, nothing is
 * recomputed. Callers pick the range to suit the text drawn on top (the tier
 * ladder carries ink only; ranking rows also carry muted secondary text, so
 * they use a lighter ceiling to keep that text readable).
 */
export function tierTint(
  color: string,
  index: number,
  count: number,
  maxPct: number,
  minPct: number,
): string {
  const t = count > 1 ? Math.min(Math.max(index / (count - 1), 0), 1) : 0;
  const pct = Math.round(maxPct - (maxPct - minPct) * t);
  return `color-mix(in srgb, ${color} ${pct}%, var(--color-surface))`;
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
