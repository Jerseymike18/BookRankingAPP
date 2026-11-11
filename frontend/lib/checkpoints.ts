/* Mid-book reviews — pure presentation logic (no engine math).
 *
 * A checkpoint's gut score is ONE 0–10 number; the prediction is a WA. They are
 * different measures, so this only places the gut score against the served 80%
 * band as a rough read — it is never a forecast test and never feeds anything. */

import type { FinishedCheckpointBook, ReadingCheckpoint, StatusSlot } from "./types";

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

/** The checkpoint furthest into the book (latest logged on a tie) — the
 *  reader's last word before finishing. Null for an empty list. */
export function latestCheckpoint(cps: ReadingCheckpoint[]): ReadingCheckpoint | null {
  return sortCheckpoints(cps)[0] ?? null;
}

export interface GutVsFinalSummary {
  /** Finished books with at least one checkpoint. */
  books: number;
  /** Mean |latest gut − final WA| over all those books. */
  gutMae: number | null;
  /** Books that also carry a frozen prediction, and the two MAEs on exactly
   *  that subset — so the comparison is like for like. */
  paired: number;
  pairedGutMae: number | null;
  pairedPredMae: number | null;
}

const mean = (xs: number[]) =>
  xs.length ? Math.round((xs.reduce((a, b) => a + b, 0) / xs.length) * 100) / 100 : null;

/** Presentation-only summary: how close the last gut score landed to the
 *  final WA, and (on the books that have one) how close the prediction did. */
export function summarizeGutVsFinal(books: FinishedCheckpointBook[]): GutVsFinalSummary {
  const gut: number[] = [];
  const pairedGut: number[] = [];
  const pairedPred: number[] = [];
  for (const b of books) {
    const last = latestCheckpoint(b.checkpoints);
    if (!last) continue;
    const g = Math.abs(last.gut_score - b.final_wa);
    gut.push(g);
    if (b.predicted_wa != null) {
      pairedGut.push(g);
      pairedPred.push(Math.abs(b.predicted_wa - b.final_wa));
    }
  }
  return {
    books: gut.length,
    gutMae: mean(gut),
    paired: pairedGut.length,
    pairedGutMae: mean(pairedGut),
    pairedPredMae: mean(pairedPred),
  };
}
