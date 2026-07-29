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
 * Display label for a component score. The 2026 nonfiction schema keeps stable
 * DB / weight-table keys ("Entertainment", "Insights") but shows friendlier
 * names in the UI ("Enjoyment", "Insight"). Fiction is unchanged. `kind` is a
 * plain string so this stays dependency-free (a BookKind is assignable to it).
 */
const NF_COMPONENT_LABELS: Record<string, string> = {
  Entertainment: "Enjoyment",
  Insights: "Insight",
};

export function componentLabel(comp: string, kind: string = "fiction"): string {
  return kind === "nonfiction" ? NF_COMPONENT_LABELS[comp] ?? comp : comp;
}
