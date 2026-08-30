import { describe, expect, it } from "vitest";
import {
  EMPTY_SAVED,
  fromWelcomeDraft,
  isEmptyDraft,
  toWelcomeDraft,
  type WelcomeDraft,
} from "../lib/welcome-draft";

/* The welcome wizard now saves as it goes: each window is written to the server on
 * the way past, and the whole in-progress draft is mirrored to sessionStorage so an
 * interrupted wizard resumes where it stopped. This codec is the part that fails
 * silently — a reader only finds out on reload, when their setup is gone or the
 * page white. Node env, pure functions, no React (frontend/AGENTS.md). */

const STEPS = 5;

const draft = (over: Partial<WelcomeDraft> = {}): WelcomeDraft => ({
  step: 2,
  mode: "customize",
  lengthPref: 0.5,
  favAuthors: ["Tolkien", ""],
  favGenres: ["Epic Fantasy", ""],
  anchorMode: "customize",
  anchorRaw: { loved: "9", liked: "7.5" },
  weightRaw: { "Epic Fantasy": { Story: "30", Character: "25" } },
  saved: { prefs: true, weights: ["Epic Fantasy"], anchors: false },
  ...over,
});

const roundTrip = (d: WelcomeDraft) =>
  fromWelcomeDraft(JSON.stringify(toWelcomeDraft(d)), STEPS);

describe("welcome draft round-trip", () => {
  it("preserves every answer a reader typed", () => {
    expect(roundTrip(draft())).toEqual(draft());
  });

  it("keeps which windows are already on the server", () => {
    // This is what stops a resumed wizard re-sending saved work — and what lets it
    // still know what it owes.
    const d = draft({ saved: { prefs: true, weights: ["A", "B"], anchors: true } });
    expect(roundTrip(d)!.saved).toEqual({ prefs: true, weights: ["A", "B"], anchors: true });
  });
});

describe("restoring a hostile or stale blob", () => {
  it("returns null rather than throwing on junk", () => {
    for (const raw of ["", "{", "null", '"a string"', "[1,2,3]", "42"]) {
      expect(fromWelcomeDraft(raw, STEPS)).toBeNull();
    }
  });

  it("fills in every field a truncated blob is missing", () => {
    // A blob written by an older build must restore as an empty wizard, not as a
    // crash on the first `.map` or a `step` of undefined.
    const d = fromWelcomeDraft("{}", STEPS)!;
    expect(d).toEqual({
      step: 0,
      mode: "keep",
      lengthPref: 0,
      favAuthors: [],
      favGenres: [],
      anchorMode: "keep",
      anchorRaw: {},
      weightRaw: {},
      saved: EMPTY_SAVED,
    });
    expect(isEmptyDraft(d)).toBe(true);
  });

  it("drops wrong-typed values instead of restoring them", () => {
    const d = fromWelcomeDraft(
      JSON.stringify({
        step: "two",
        mode: "nonsense",
        lengthPref: "long",
        favAuthors: ["ok", 7, null],
        favGenres: "Epic Fantasy",
        anchorMode: 3,
        anchorRaw: { good: 9, fine: "7" },
        weightRaw: { "Epic Fantasy": { Story: 30, Character: "25" } },
        saved: { prefs: "yes", weights: [1, "A"], anchors: 1 },
      }),
      STEPS
    )!;
    expect(d.step).toBe(0);
    expect(d.mode).toBe("keep");
    expect(d.anchorMode).toBe("keep");
    expect(d.lengthPref).toBe(0);
    expect(d.favAuthors).toEqual(["ok"]);
    expect(d.favGenres).toEqual([]);
    expect(d.anchorRaw).toEqual({ fine: "7" });
    expect(d.weightRaw).toEqual({ "Epic Fantasy": { Character: "25" } });
    // Anything not exactly `true` is not "already saved" — guessing yes here would
    // skip a write the reader never got.
    expect(d.saved).toEqual({ prefs: false, weights: ["A"], anchors: false });
  });

  it("never restores a window the wizard no longer has", () => {
    // A draft from a longer wizard must not land on a step that isn't rendered.
    expect(fromWelcomeDraft(JSON.stringify(draft({ step: 9 })), STEPS)!.step).toBe(4);
    expect(fromWelcomeDraft(JSON.stringify(draft({ step: -3 })), STEPS)!.step).toBe(0);
    expect(fromWelcomeDraft(JSON.stringify(draft({ step: 2.7 })), STEPS)!.step).toBe(2);
  });

  it("bounds the favourites lists so a blob can't grow without limit", () => {
    const many = Array.from({ length: 500 }, (_, i) => `a${i}`);
    expect(fromWelcomeDraft(JSON.stringify(draft({ favAuthors: many })), STEPS)!
      .favAuthors.length).toBe(20);
  });
});

describe("isEmptyDraft", () => {
  it("is true for an untouched wizard", () => {
    expect(
      isEmptyDraft({
        step: 0,
        mode: "keep",
        lengthPref: 0,
        favAuthors: ["", "", ""],
        favGenres: ["", ""],
        anchorMode: "keep",
        anchorRaw: { loved: "9" },
        weightRaw: {},
        saved: EMPTY_SAVED,
      })
    ).toBe(true);
  });

  it("is false as soon as anything is answered or saved", () => {
    const base = {
      step: 0,
      mode: "keep" as const,
      lengthPref: 0,
      favAuthors: [""],
      favGenres: [""],
      anchorMode: "keep" as const,
      anchorRaw: {},
      weightRaw: {},
      saved: EMPTY_SAVED,
    };
    expect(isEmptyDraft({ ...base, step: 1 })).toBe(false);
    expect(isEmptyDraft({ ...base, lengthPref: -1 })).toBe(false);
    expect(isEmptyDraft({ ...base, favAuthors: ["Tolkien"] })).toBe(false);
    expect(isEmptyDraft({ ...base, favGenres: ["Epic Fantasy"] })).toBe(false);
    expect(isEmptyDraft({ ...base, mode: "customize" })).toBe(false);
    expect(isEmptyDraft({ ...base, anchorMode: "customize" })).toBe(false);
    // Already-committed work must never look empty — an empty draft is not written,
    // so a resumed wizard would forget that these windows are on the server.
    expect(isEmptyDraft({ ...base, saved: { ...EMPTY_SAVED, prefs: true } })).toBe(false);
    expect(isEmptyDraft({ ...base, saved: { ...EMPTY_SAVED, weights: ["A"] } })).toBe(false);
    expect(isEmptyDraft({ ...base, saved: { ...EMPTY_SAVED, anchors: true } })).toBe(false);
  });
});
