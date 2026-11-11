/* Mid-book reviews — pure presentation logic (no engine math).
 *
 * A checkpoint's gut score is ONE 0–10 number; the prediction is a WA. They are
 * different measures, so this only places the gut score against the served 80%
 * band as a rough read — it is never a forecast test and never feeds anything. */

import type { ReadingCheckpoint, StatusSlot } from "./types";

export type CheckpointPlacement = "above" | "within" | "below";

/** Where a gut score sits against the slot's served band. Null when the slot
 *  carries no band (nonfiction, no prediction, or no residual table loaded) —
 *  a band is never invented from the point estimate. */
export function placeAgainstBand(
  gutScore: number,
  slot: Pick<StatusSlot, "wa_low" | "wa_high">,
): CheckpointPlacement | null {
  const { wa_low, wa_high } = slot;
  if (wa_low == null || wa_high == null) return null;
  if (gutScore > wa_high) return "above";
  if (gutScore < wa_low) return "below";
  return "within";
}

/** Signed gap from the slot's WA (gut − WA), or null with no WA. */
export function gapFromWa(gutScore: number, wa: number | null): number | null {
  if (wa == null) return null;
  return Math.round((gutScore - wa) * 100) / 100;
}

/** Newest first for display: by progress, then by when it was logged. */
export function sortCheckpoints(cps: ReadingCheckpoint[]): ReadingCheckpoint[] {
  return [...cps].sort(
    (a, b) => b.progress_pct - a.progress_pct || b.created_at.localeCompare(a.created_at),
  );
}

/** Parse a form field into a valid value, or null. */
export function parseProgress(raw: string): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 && n <= 100 ? n : null;
}

export function parseGutScore(raw: string): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 && n <= 10 ? Math.round(n * 10) / 10 : null;
}
