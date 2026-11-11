import { describe, expect, it } from "vitest";
import {
  gapFromWa,
  latestCheckpoint,
  parseGutScore,
  parseProgress,
  placeAgainstBand,
  sortCheckpoints,
  summarizeGutVsFinal,
} from "../lib/checkpoints";
import type { FinishedCheckpointBook, ReadingCheckpoint } from "../lib/types";

describe("placeAgainstBand", () => {
  const slot = { wa_low: 6.5, wa_high: 8.5 };
  it("places inside, above and below the band (edges count as within)", () => {
    expect(placeAgainstBand(7, slot)).toBe("within");
    expect(placeAgainstBand(6.5, slot)).toBe("within");
    expect(placeAgainstBand(8.5, slot)).toBe("within");
    expect(placeAgainstBand(9, slot)).toBe("above");
    expect(placeAgainstBand(5, slot)).toBe("below");
  });
  it("never invents a band when none is served", () => {
    expect(placeAgainstBand(7, {})).toBeNull();
    expect(placeAgainstBand(7, { wa_low: 6 })).toBeNull();
  });
});

describe("gapFromWa", () => {
  it("is gut minus WA, rounded to 2dp", () => {
    expect(gapFromWa(8, 7.25)).toBe(0.75);
    expect(gapFromWa(6, 7.333)).toBe(-1.33);
    expect(gapFromWa(6, null)).toBeNull();
  });
});

describe("sortCheckpoints", () => {
  const cp = (id: string, pct: number, at: string): ReadingCheckpoint => ({
    id, kind: "fiction", title: "T", progress_pct: pct, gut_score: 7, note: null, created_at: at,
  });
  it("sorts furthest-in first, ties by newest", () => {
    const out = sortCheckpoints([
      cp("a", 20, "2026-11-01T00:00:00+00:00"),
      cp("b", 60, "2026-11-05T00:00:00+00:00"),
      cp("c", 60, "2026-11-06T00:00:00+00:00"),
    ]);
    expect(out.map((c) => c.id)).toEqual(["c", "b", "a"]);
  });
});

describe("form parsing", () => {
  it("accepts whole-number progress 0–100 only", () => {
    expect(parseProgress("45")).toBe(45);
    expect(parseProgress("0")).toBe(0);
    expect(parseProgress("100")).toBe(100);
    expect(parseProgress("")).toBeNull();
    expect(parseProgress("101")).toBeNull();
    expect(parseProgress("4.5")).toBeNull();
  });
  it("accepts gut scores 0–10, rounded to one decimal", () => {
    expect(parseGutScore("7.5")).toBe(7.5);
    expect(parseGutScore("7.25")).toBe(7.3);
    expect(parseGutScore("10")).toBe(10);
    expect(parseGutScore("11")).toBeNull();
    expect(parseGutScore("abc")).toBeNull();
    expect(parseGutScore(" ")).toBeNull();
  });
});

describe("gut vs final", () => {
  const cp = (pct: number, gut: number, at = "2026-11-01T00:00:00+00:00"): ReadingCheckpoint => ({
    id: `${pct}-${gut}`, kind: "fiction", title: "T", progress_pct: pct, gut_score: gut, note: null, created_at: at,
  });
  const book = (final_wa: number, predicted_wa: number | null, cps: ReadingCheckpoint[]): FinishedCheckpointBook => ({
    title: "T", author: "A", genre: "G", final_wa, predicted_wa, checkpoints: cps,
  });

  it("latestCheckpoint is the furthest-in one", () => {
    expect(latestCheckpoint([cp(20, 5), cp(80, 9), cp(50, 7)])?.progress_pct).toBe(80);
    expect(latestCheckpoint([])).toBeNull();
  });

  it("uses the latest gut score, and pairs MAEs on the same books only", () => {
    const s = summarizeGutVsFinal([
      book(8, 7, [cp(30, 5), cp(90, 8.5)]),  // gut |0.5|, pred |1|
      book(6, null, [cp(50, 7)]),            // gut |1|, no prediction
      book(7, 7.5, []),                      // no checkpoints → ignored
    ]);
    expect(s.books).toBe(2);
    expect(s.gutMae).toBe(0.75);
    expect(s.paired).toBe(1);
    expect(s.pairedGutMae).toBe(0.5);
    expect(s.pairedPredMae).toBe(1);
  });

  it("is empty-safe", () => {
    expect(summarizeGutVsFinal([])).toEqual({
      books: 0, gutMae: null, paired: 0, pairedGutMae: null, pairedPredMae: null,
    });
  });
});
