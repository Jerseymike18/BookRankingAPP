import { describe, it, expect } from "vitest";
import { tierTint, positionsInGroups } from "../lib/tier-tint";

describe("tierTint", () => {
  it("runs from max at the tier's best book to min at its lowest", () => {
    expect(tierTint("red", 0, 5, 40, 12)).toBe("color-mix(in srgb, red 40%, var(--color-surface))");
    expect(tierTint("red", 4, 5, 40, 12)).toBe("color-mix(in srgb, red 12%, var(--color-surface))");
    expect(tierTint("red", 2, 5, 40, 12)).toBe("color-mix(in srgb, red 26%, var(--color-surface))");
  });
  it("gives a lone book the full tint", () => {
    expect(tierTint("red", 0, 1, 22, 6)).toContain("red 22%");
  });
});

describe("positionsInGroups", () => {
  it("ranks best-first within each group, independent of input order", () => {
    const p = positionsInGroups([
      { key: "a", group: "S", score: 8.6 },
      { key: "b", group: "A", score: 7.9 },
      { key: "c", group: "S", score: 9.1 },
      { key: "d", group: "S", score: 8.9 },
    ]);
    expect(p.get("c")).toEqual({ index: 0, count: 3 });
    expect(p.get("d")).toEqual({ index: 1, count: 3 });
    expect(p.get("a")).toEqual({ index: 2, count: 3 });
    expect(p.get("b")).toEqual({ index: 0, count: 1 });
  });
});
