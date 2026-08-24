/**
 * The Predict session-snapshot codec (`lib/predict-jobs`).
 *
 * WHY THIS EXISTS
 * ---------------
 * `toSnapshot`/`fromSnapshot` mirror a reader's Predict state into
 * sessionStorage. Every way this can be wrong is invisible until someone
 * reloads, and then it is either "my work vanished" or a white page:
 *
 *  - a spinner restored as `true` waits on a fetch the reload already killed;
 *  - a stale error banner insists the app is broken after the reader fixed it;
 *  - a corrupt blob reaches `result.genres.map` and takes the page down on
 *    load, recoverable only by clearing storage by hand;
 *  - a snapshot written before genre prediction existed must still hydrate,
 *    because the key was deliberately NOT version-bumped (bumping it would
 *    have discarded the in-flight runs of anyone mid-scoring at deploy).
 *
 * None of that is reachable by typecheck, and the repo's other tests are
 * Python. Hence this file.
 */
import { describe, expect, it } from "vitest";
import { EMPTY_GENRE_TAB, toSnapshot, fromSnapshot } from "@/lib/predict-jobs";
import type { GenreTabState } from "@/lib/predict-jobs";
import type { GenreRecommendResponse } from "@/lib/types";

/** Minimal payload with the three arrays the restore shape-checks. */
const RESULT = {
  genres: [{ genre: "Gothic Fiction" }],
  types: [],
  evidence: [{ genre: "Gothic Fiction" }],
} as unknown as GenreRecommendResponse;

const EMPTY_RUNS = {
  fiction: { request: "", candidates: null, scored: [] },
  nonfiction: { request: "", candidates: null, scored: [] },
  // The codec only reads the durable fields; a partial run is enough here and
  // keeps the fixture honest about what this file is actually testing.
} as never;

function roundTrip(genreTab: GenreTabState) {
  const restored = fromSnapshot(JSON.stringify(toSnapshot(EMPTY_RUNS, genreTab)));
  if (!restored) throw new Error("snapshot failed to parse");
  return restored.genreTab;
}

describe("genre recommendation survives a reload", () => {
  it("round-trips the result, the focus text and the evidence toggle", () => {
    const out = roundTrip({
      ...EMPTY_GENRE_TAB,
      focus: "branch out",
      result: RESULT,
      showEvidence: true,
    });
    expect(out.result).toEqual(RESULT);
    expect(out.focus).toBe("branch out");
    expect(out.showEvidence).toBe(true);
    expect(out.interrupted).toBe(null);
  });
});

describe("a reload that lands MID-CALL", () => {
  const out = roundTrip({ ...EMPTY_GENRE_TAB, loading: true, focus: "x" });

  it("never restores a spinner — the fetch it waited on is gone", () => {
    expect(out.loading).toBe(false);
  });

  it("says so instead of looking idle", () => {
    expect(out.interrupted).toBeTruthy();
    expect(out.interrupted).toMatch(/reloaded/i);
  });

  it("keeps what the reader typed, so retrying is one click", () => {
    expect(out.focus).toBe("x");
  });
});

describe("what must NOT be persisted", () => {
  it("drops the error — it describes a moment the reload already ended", () => {
    const out = roundTrip({ ...EMPTY_GENRE_TAB, error: "boom", result: RESULT });
    expect(out.error).toBe(null);
    expect(out.result).toEqual(RESULT); // ...without losing the good result
  });
});

describe("backwards compatibility (STORAGE_KEY was NOT bumped)", () => {
  it("hydrates a snapshot written before genre prediction existed", () => {
    const legacy = fromSnapshot(JSON.stringify({ fiction: {}, nonfiction: {} }));
    expect(legacy).not.toBe(null);
    expect(legacy!.genreTab).toEqual(EMPTY_GENRE_TAB);
  });
});

describe("a corrupt payload must never reach the render", () => {
  // Each of these would previously have hit `result.genres.map` on load.
  const corrupt: [string, unknown][] = [
    ["a bare string", "a string"],
    ["a number", 42],
    ["an empty object", {}],
    ["genres present but not an array", { genres: "nope", types: [], evidence: [] }],
    ["a half-written object", { genres: [] }],
  ];
  it.each(corrupt)("rejects %s", (_label, bad) => {
    const raw = JSON.stringify({
      fiction: {},
      nonfiction: {},
      genreTab: { focus: "", showEvidence: false, busy: false, result: bad },
    });
    expect(fromSnapshot(raw)!.genreTab.result).toBe(null);
  });

  it("returns null for unparseable JSON rather than throwing", () => {
    expect(fromSnapshot("{{{")).toBe(null);
  });

  it("returns null for JSON that is not an object", () => {
    expect(fromSnapshot("[1,2,3]")!.genreTab).toEqual(EMPTY_GENRE_TAB);
  });
});
