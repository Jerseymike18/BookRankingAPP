import { describe, expect, it } from "vitest";
import {
  fromAddBookDraft,
  isEmptyAddBookDraft,
  scoredCount,
  type AddBookDraft,
} from "../lib/add-book-draft";

/* /add-book is the heaviest data entry in the app — 14 component boxes plus the
 * metadata — and it now survives a navigation via a sessionStorage draft. This
 * codec is where that fails silently: a reader only finds out on return, when the
 * restore hands them a genre they deleted, a score for a component of the other
 * kind, or a white page. Node env, pure functions, no React. */

const FICTION = ["Plot", "Entertainment", "Prose", "Depth2"];
const NONFICTION = ["Substance", "Reasoning"];
const components = (kind: string) => (kind === "nonfiction" ? NONFICTION : FICTION);
const GENRES = ["Epic Fantasy", "Literary Fiction"];

const parse = (o: unknown) => fromAddBookDraft(JSON.stringify(o), components, GENRES);

const full: AddBookDraft = {
  kind: "fiction",
  title: "The Way of Kings",
  author: "Brandon Sanderson",
  genre: "Epic Fantasy",
  series: "The Stormlight Archive",
  seriesNumber: 1,
  words: 387000,
  yearRead: 2026,
  monthRead: 3,
  scores: { Plot: "9", Entertainment: "9.5", Prose: "", Depth2: "8" },
  prefilled: true,
};

describe("add-book draft round-trip", () => {
  it("preserves everything typed, empty boxes included", () => {
    // An empty box means "not rated yet" and must come back empty — not as a 0,
    // which is a real score the engine would weight.
    expect(parse(full)).toEqual(full);
  });
});

describe("restoring against current server truth", () => {
  it("drops a genre the reader no longer has", () => {
    // Restoring a deleted genre would put the select in a state the backend
    // rejects on submit, after the reader has re-typed 14 scores.
    expect(parse({ ...full, genre: "Gothic Fiction" })!.genre).toBe("");
    expect(parse({ ...full, genre: 42 })!.genre).toBe("");
  });

  it("drops scores that aren't components of the restored kind", () => {
    const d = parse({
      ...full,
      kind: "nonfiction",
      scores: { Substance: "8", Plot: "9", Bogus: "7" },
    })!;
    expect(d.kind).toBe("nonfiction");
    expect(d.scores).toEqual({ Substance: "8" });
  });

  it("falls back to fiction for an unknown kind", () => {
    expect(parse({ ...full, kind: "poetry" })!.kind).toBe("fiction");
  });
});

describe("restoring a hostile or stale blob", () => {
  it("returns null rather than throwing on junk", () => {
    for (const raw of ["", "{", "null", '"a string"', "[1,2]", "7"]) {
      expect(fromAddBookDraft(raw, components, GENRES)).toBeNull();
    }
  });

  it("fills in every field a truncated blob is missing", () => {
    const d = fromAddBookDraft("{}", components, GENRES)!;
    expect(d.title).toBe("");
    expect(d.author).toBe("");
    expect(d.series).toBe("");
    expect(d.seriesNumber).toBeNull();
    expect(d.words).toBe(0);
    expect(d.scores).toEqual({});
    expect(d.prefilled).toBe(false);
    expect(Number.isFinite(d.yearRead)).toBe(true);
    expect(d.monthRead).toBeGreaterThanOrEqual(1);
    expect(isEmptyAddBookDraft(d)).toBe(true);
  });

  it("drops wrong-typed values instead of restoring them", () => {
    const d = parse({
      ...full,
      title: 12,
      seriesNumber: "one",
      words: "many",
      yearRead: null,
      scores: { Plot: 9, Entertainment: "9" },
      prefilled: "yes",
    })!;
    expect(d.title).toBe("");
    expect(d.seriesNumber).toBeNull();
    expect(d.words).toBe(0);
    expect(Number.isFinite(d.yearRead)).toBe(true);
    expect(d.scores).toEqual({ Entertainment: "9" });
    expect(d.prefilled).toBe(false);
  });

  it("never restores a negative word count", () => {
    expect(parse({ ...full, words: -5 })!.words).toBe(0);
  });
});

describe("isEmptyAddBookDraft", () => {
  const blank: AddBookDraft = {
    kind: "fiction", title: "", author: "", genre: "Epic Fantasy", series: "",
    seriesNumber: null, words: 0, yearRead: 2026, monthRead: 3,
    scores: { Plot: "", Entertainment: "" }, prefilled: false,
  };

  it("is true for an untouched form", () => {
    expect(isEmptyAddBookDraft(blank)).toBe(true);
    // Whitespace is not entry.
    expect(isEmptyAddBookDraft({ ...blank, title: "   ", scores: { Plot: " " } })).toBe(true);
  });

  it("ignores the date, which defaults to today rather than being typed", () => {
    expect(isEmptyAddBookDraft({ ...blank, yearRead: 1999, monthRead: 12 })).toBe(true);
  });

  it("is false as soon as anything real is entered", () => {
    expect(isEmptyAddBookDraft({ ...blank, title: "Dune" })).toBe(false);
    expect(isEmptyAddBookDraft({ ...blank, author: "Herbert" })).toBe(false);
    expect(isEmptyAddBookDraft({ ...blank, series: "Dune" })).toBe(false);
    expect(isEmptyAddBookDraft({ ...blank, seriesNumber: 1 })).toBe(false);
    expect(isEmptyAddBookDraft({ ...blank, words: 1000 })).toBe(false);
    expect(isEmptyAddBookDraft({ ...blank, scores: { Plot: "8" } })).toBe(false);
  });
});

describe("scoredCount", () => {
  it("counts only boxes with a value", () => {
    expect(scoredCount(full)).toBe(3); // Prose is empty
    expect(scoredCount({ ...full, scores: {} })).toBe(0);
  });
});
