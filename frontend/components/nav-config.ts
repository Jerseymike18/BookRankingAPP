/**
 * NAV CONFIG — the one source of truth for the app shell's destinations.
 *
 * Both layouts of the shell read from here:
 *   - desktop (md+): a persistent left sidebar listing every group, expanded;
 *   - phone (<md): a bottom tab bar (one tab per primary group + "More"), with a
 *     sub-tab strip under the top bar for the active group's sibling pages.
 *
 * Pure data + pure functions only (no React), so it is unit-testable in the
 * vitest node env.
 */
import { READONLY } from "@/lib/readonly";

/** `short` is the label in the phone section-tab strip, where width is tight. */
export type NavItem = { href: string; label: string; short?: string };

/** A nav group. `tab` = it gets its own slot in the phone tab bar; groups
 *  without it live behind the "More" tab. `icon` keys into the shell's icon set. */
export type NavGroup = {
  id: string;
  label: string;
  items: NavItem[];
  tab?: boolean;
  icon: IconName;
};

export type IconName = "stats" | "reading" | "predict" | "explore" | "community" | "nerds" | "more";

// Stats — the merged Rankings + Stats page, the home screen (/ redirects here).
const STATS_ITEMS: NavItem[] = [{ href: "/stats", label: "Stats" }];

// Reading — the currently-reading view, the single de-duplicated Read Queue, and
// (write-only) Import. Add a Book is not a nav item: the floating "+" opens it
// as a sheet from any page (components/Nav.tsx AddBookFab). Import is dropped
// on the read-only build (the page also self-guards).
const READING_ITEMS: NavItem[] = [
  { href: "/read-queue", label: "Read Queue", short: "Queue" },
  { href: "/reading", label: "Currently Reading", short: "Current" },
  ...(READONLY ? [] : [{ href: "/import", label: "Import" }]),
];

// Predict — book prediction (the daily errand) and genre prediction (the
// occasional one). Nested hrefs: see activeItemHref.
const PREDICT_ITEMS: NavItem[] = [
  { href: "/predict", label: "Book Prediction", short: "Books" },
  { href: "/predict/genres", label: "Genre Prediction", short: "Genres" },
];

// Explore — read-only-safe analytical views, each with its own in-page
// Fiction / Nonfiction toggle.
const EXPLORE_ITEMS: NavItem[] = [
  { href: "/tier-list", label: "Tier List" },
  { href: "/series", label: "Series" },
  { href: "/timeline", label: "Timeline" },
];

// Community — public profiles; needs the live backend + auth.
const COMMUNITY_ITEMS: NavItem[] = [
  { href: "/directory", label: "Directory" },
  { href: "/profile", label: "My Profile", short: "Profile" },
];

// For Nerds — how-it-works + diagnostics. Genre Weights + Tutorial are per-user
// and dropped on the read-only build. Delta Log is intentionally absent (the
// DeltaTracker layer is retired).
const NERD_ITEMS: NavItem[] = [
  { href: "/methodology", label: "Methodology" },
  { href: "/track-record", label: "Track Record" },
  { href: "/series-breakdown", label: "Series Breakdown", short: "Series Math" },
  { href: "/calibration", label: "Calibration" },
  { href: "/analytics", label: "Taste Lab" },
  ...(READONLY
    ? []
    : [
        { href: "/weights", label: "Genre Weights", short: "Weights" },
        { href: "/welcome", label: "Tutorial" },
      ]),
];

export const NAV_GROUPS: NavGroup[] = [
  { id: "stats", label: "Stats", items: STATS_ITEMS, tab: true, icon: "stats" },
  { id: "reading", label: "Reading", items: READING_ITEMS, tab: true, icon: "reading" },
  ...(READONLY
    ? []
    : [{ id: "predict", label: "Predict", items: PREDICT_ITEMS, tab: true, icon: "predict" as const }]),
  { id: "explore", label: "Explore", items: EXPLORE_ITEMS, tab: true, icon: "explore" },
  ...(READONLY
    ? []
    : [{ id: "community", label: "Community", items: COMMUNITY_ITEMS, icon: "community" as const }]),
  { id: "nerds", label: "For Nerds", items: NERD_ITEMS, icon: "nerds" },
];

/** Which item of a section the current path belongs to — LONGEST match wins.
 *
 *  Predict's items NEST (`/predict` and `/predict/genres`), and there a prefix
 *  test lights up BOTH. Returns the active item's href, or null when the path is
 *  outside this section. The `href + "/"` boundary also stops "/predictfoo"
 *  matching "/predict". */
export function activeItemHref(items: NavItem[], currentPath: string): string | null {
  let best: string | null = null;
  for (const { href } of items) {
    const hit = currentPath === href || currentPath.startsWith(href + "/");
    if (hit && (best === null || href.length > best.length)) best = href;
  }
  return best;
}

/** Extra path → group claims for routes that are not nav items themselves but
 *  belong under one (so the right tab stays lit and the title is sensible). */
const ROUTE_GROUP_HINTS: { prefix: string; group: string }[] = [
  { prefix: "/u", group: "community" },
  { prefix: "/add-book", group: "reading" },
  { prefix: "/edit-ratings", group: "stats" },
  { prefix: "/rankings", group: "stats" },
];

/** The group the current path lives in, or null (e.g. /login, /try). When two
 *  groups both match, the longer matching href wins — same rule as within one. */
export function activeGroup(groups: NavGroup[], currentPath: string): NavGroup | null {
  let best: { g: NavGroup; len: number } | null = null;
  for (const g of groups) {
    const href = activeItemHref(g.items, currentPath);
    if (href !== null && (best === null || href.length > best.len)) best = { g, len: href.length };
  }
  if (best) return best.g;
  for (const { prefix, group } of ROUTE_GROUP_HINTS) {
    if (currentPath === prefix || currentPath.startsWith(prefix + "/")) {
      return groups.find((g) => g.id === group) ?? null;
    }
  }
  return null;
}

/** True when the path is a detail page beneath a nav destination rather than a
 *  destination itself (e.g. /u/<handle>) — the phone top bar shows Back there. */
export function isDetailRoute(groups: NavGroup[], currentPath: string): boolean {
  for (const g of groups) for (const { href } of g.items) if (href === currentPath) return false;
  return currentPath.split("/").filter(Boolean).length > 1;
}

/** Routes rendered with no shell chrome at all (a full-screen flow). */
export function isChromeless(currentPath: string): boolean {
  return currentPath === "/login" || currentPath.startsWith("/login/");
}
