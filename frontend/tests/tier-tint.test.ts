import { describe, it, expect } from "vitest";
import { positionsInGroups, buildTierIndex, tierSpineClass, tierBadgeFill } from "../lib/tier-tint";

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


describe("buildTierIndex", () => {
  const order = ["S+", "S", "A"];
  it("uses the server's tier label and orders within it best-first, as the Tier List does", () => {
    const idx = buildTierIndex(
      [
        { title: "x", tier: "S", wa: 8.7 },
        { title: "y", tier: "S", wa: 9.1 },
        // A server tier wins over WA: a 9.6 that the cohort put in S stays S.
        { title: "z", tier: "S", wa: 9.6 },
        { title: "w", tier: "A", wa: 8.0 },
      ],
      order,
      "fiction",
    );
    expect(idx.z).toEqual({ tier: "S", index: 0, count: 3 });
    expect(idx.y).toEqual({ tier: "S", index: 1, count: 3 });
    expect(idx.x).toEqual({ tier: "S", index: 2, count: 3 });
    expect(idx.w).toEqual({ tier: "A", index: 0, count: 1 });
  });
  it("orders nonfiction by Total Average, like the nonfiction Tier List", () => {
    const idx = buildTierIndex(
      [
        { title: "a", tier: "S", wa: 9.0, total_average: 8.1 },
        { title: "b", tier: "S", wa: 8.5, total_average: 8.9 },
      ],
      order,
      "nonfiction",
    );
    expect(idx.b.index).toBe(0);
    expect(idx.a.index).toBe(1);
  });
  it("maps tier labels to spine classes", () => {
    expect(tierSpineClass("S+")).toBe("spine-sp");
    expect(tierSpineClass("F")).toBe("spine-f");
    expect(tierSpineClass("?")).toBeUndefined();
  });
});

describe("tierBadgeFill", () => {
  it("is the tier's hi shade for its best book and its lo shade for its lowest", () => {
    expect(tierBadgeFill("spine-s", 0, 4)).toBe("color-mix(in srgb, var(--badge-hi-spine-s) 100%, var(--badge-lo-spine-s))");
    expect(tierBadgeFill("spine-s", 3, 4)).toBe("color-mix(in srgb, var(--badge-hi-spine-s) 0%, var(--badge-lo-spine-s))");
    expect(tierBadgeFill("spine-a", 0, 1)).toContain("100%");
    expect(tierBadgeFill("spine-b", 1, 3, "circle")).toBe("color-mix(in srgb, var(--circle-hi-spine-b) 50%, var(--circle-lo-spine-b))");
  });
});
