import { describe, it, expect } from "vitest";
import { standingText } from "../lib/format";

describe("standingText", () => {
  it("shows WA to 2 decimals with the rank and library size", () => {
    expect(standingText({ wa: 8.4166, rank: 12, of: 144 })).toBe("WA 8.42 · #12 of 144");
  });
});
