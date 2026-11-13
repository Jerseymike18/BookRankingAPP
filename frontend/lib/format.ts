/**
 * Format a series name with its ordinal, e.g. "The Wheel of Time #3".
 * Returns the bare series name when no ordinal is known, and "" when there is
 * no series. series_number may be fractional (0.5 prequels, 3.5 interstitials).
 */
export function seriesLabel(
  series: string | null | undefined,
  seriesNumber: number | null | undefined,
): string {
  const name = (series ?? "").trim();
  if (!name) return "";
  if (seriesNumber == null) return name;
  return `${name} #${seriesNumber}`;
}

/**
 * Display label for a component score. Both schemas keep stable DB /
 * weight-table keys but may show friendlier names in the UI: nonfiction
 * "Entertainment"/"Insights" read "Enjoyment"/"Insight", and fiction
 * "Narration" reads "Voice" and "Depth2" reads "World Depth" (owner renames
 * 2026-11-13 — the stored keys and every rating under them are unchanged). `kind` is a plain string so this stays
 * dependency-free (a BookKind is assignable to it).
 */
const FICTION_COMPONENT_LABELS: Record<string, string> = {
  Narration: "Voice",
  // Worldbuilding's depth component is stored as "Depth2" (Character already
  // owns "Depth"); "World Depth" keeps the two distinct in flat lists.
  Depth2: "World Depth",
};

const NF_COMPONENT_LABELS: Record<string, string> = {
  Entertainment: "Enjoyment",
  Insights: "Insight",
};

export function componentLabel(comp: string, kind: string = "fiction"): string {
  const labels = kind === "nonfiction" ? NF_COMPONENT_LABELS : FICTION_COMPONENT_LABELS;
  return labels[comp] ?? comp;
}

/** "WA 8.42 · #12 of 144" — a book's standing, for the add notice and preview. */
export function standingText(s: { wa: number; rank: number; of: number }): string {
  return `WA ${s.wa.toFixed(2)} · #${s.rank} of ${s.of}`;
}
