/**
 * Nav active-state resolution (`components/Nav.activeItemHref`).
 *
 * WHY THIS EXISTS
 * ---------------
 * Every nav group had flat, mutually-exclusive hrefs, so a plain
 * `currentPath.startsWith(href)` was correct for years. Predict is the first
 * group whose items NEST — `/predict` and `/predict/genres` — and there that
 * test lights up BOTH entries, because "/predict/genres".startsWith("/predict")
 * is true. Longest match wins now.
 *
 * The failure is purely visual, which is exactly why it needs a test: nothing
 * throws, nothing typechecks wrong, and two highlighted nav items look enough
 * like a design choice to survive review. The next nested route added to this
 * app will silently depend on this behaviour.
 */
import { describe, expect, it } from "vitest";
import { activeItemHref } from "@/components/Nav";

const PREDICT = [
  { href: "/predict", label: "Book Prediction" },
  { href: "/predict/genres", label: "Genre Prediction" },
];

// A flat group, to prove the fix did not change the behaviour every other
// section in the nav relies on.
const READING = [
  { href: "/reading", label: "Currently Reading" },
  { href: "/read-queue", label: "Read Queue" },
];

describe("nested siblings — the case this function exists for", () => {
  it("the CHILD wins on the child route, not the parent prefix", () => {
    expect(activeItemHref(PREDICT, "/predict/genres")).toBe("/predict/genres");
  });

  it("the parent wins on the parent route", () => {
    expect(activeItemHref(PREDICT, "/predict")).toBe("/predict");
  });

  it("order in the array does not decide it — length does", () => {
    expect(activeItemHref([...PREDICT].reverse(), "/predict/genres")).toBe("/predict/genres");
  });

  it("an unknown child still activates its parent group", () => {
    // A future /predict/something-else should light Book Prediction rather than
    // leaving the whole Predict group looking inactive.
    expect(activeItemHref(PREDICT, "/predict/whatever")).toBe("/predict");
  });
});

describe("boundaries", () => {
  it("a sibling route sharing a prefix does NOT match", () => {
    // "/predictfoo".startsWith("/predict") is true — the href + "/" boundary is
    // what stops that from claiming the Predict group.
    expect(activeItemHref(PREDICT, "/predictfoo")).toBe(null);
  });

  it("a path outside the section returns null (group renders inactive)", () => {
    expect(activeItemHref(PREDICT, "/stats")).toBe(null);
  });

  it("the site root does not match any section", () => {
    expect(activeItemHref(PREDICT, "/")).toBe(null);
  });
});

describe("flat groups behave exactly as before", () => {
  it.each([
    ["/reading", "/reading"],
    ["/read-queue", "/read-queue"],
    ["/read-queue/anything", "/read-queue"],
  ])("%s resolves to %s", (path, expected) => {
    expect(activeItemHref(READING, path)).toBe(expected);
  });

  it("does not confuse /reading with /read-queue", () => {
    expect(activeItemHref(READING, "/read-queue")).toBe("/read-queue");
  });

  it("an empty section resolves to null rather than throwing", () => {
    expect(activeItemHref([], "/predict")).toBe(null);
  });
});
