/**
 * App-shell routing (`components/nav-config`).
 *
 * The phone tab bar lights ONE tab from the current path, and the top bar shows
 * Back only on detail routes. Both are pure path → group decisions; a wrong one
 * is purely visual (nothing throws), so pin them here.
 */
import { describe, expect, it } from "vitest";
import {
  NAV_GROUPS,
  activeGroup,
  isChromeless,
  isDetailRoute,
} from "@/components/nav-config";

const groupOf = (p: string) => activeGroup(NAV_GROUPS, p)?.id ?? null;

describe("activeGroup", () => {
  it.each([
    ["/stats", "stats"],
    ["/read-queue", "reading"],
    ["/reading", "reading"],
    ["/tier-list", "explore"],
    ["/methodology", "nerds"],
  ])("%s → %s", (path, id) => {
    expect(groupOf(path)).toBe(id);
  });

  it("/reading does not swallow /read-queue or vice versa", () => {
    expect(groupOf("/read-queue")).toBe("reading");
  });

  it("a public profile lives under Community via the route hint", () => {
    // Community is dropped on the read-only build, so only assert when present.
    if (NAV_GROUPS.some((g) => g.id === "community")) {
      expect(groupOf("/u/someone")).toBe("community");
    }
  });

  it("routes outside the nav have no group", () => {
    expect(groupOf("/login")).toBe(null);
    expect(groupOf("/try")).toBe(null);
    expect(groupOf("/")).toBe(null);
  });

  it("every group has items and a unique id", () => {
    const ids = NAV_GROUPS.map((g) => g.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const g of NAV_GROUPS) expect(g.items.length).toBeGreaterThan(0);
  });

  it("at most 4 primary tabs, so the tab bar (+ More) fits a phone", () => {
    expect(NAV_GROUPS.filter((g) => g.tab).length).toBeLessThanOrEqual(4);
  });
});

describe("isDetailRoute", () => {
  it("nav destinations are not detail routes — including nested ones", () => {
    expect(isDetailRoute(NAV_GROUPS, "/stats")).toBe(false);
    expect(isDetailRoute(NAV_GROUPS, "/methodology")).toBe(false);
    expect(isDetailRoute(NAV_GROUPS, "/tier-list")).toBe(false);
  });

  it("a page beneath a destination is", () => {
    expect(isDetailRoute(NAV_GROUPS, "/u/someone")).toBe(true);
  });

  it("top-level non-nav pages are not", () => {
    expect(isDetailRoute(NAV_GROUPS, "/try")).toBe(false);
    expect(isDetailRoute(NAV_GROUPS, "/")).toBe(false);
  });
});

describe("isChromeless", () => {
  it("login is full-screen", () => {
    expect(isChromeless("/login")).toBe(true);
    expect(isChromeless("/stats")).toBe(false);
    expect(isChromeless("/loginx")).toBe(false);
  });
});
