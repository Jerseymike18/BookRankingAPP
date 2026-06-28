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
