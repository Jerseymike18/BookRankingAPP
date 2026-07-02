/**
 * Taste Lab analytics — pure, testable derivations over the live Book[] payload.
 *
 * Every function here takes the already-fetched Book[] and returns plain data;
 * no rendering, no fetching, no caching. The Taste Lab page recomputes these on
 * each render so a newly-added book is reflected with no extra wiring.
 *
 * ── Missing-data policy (read before "fixing" the WB handling) ──────────────
 * A component value counts unless it is `null`; only genuine nulls are skipped
 * pairwise. The API encodes "Worldbuilding not scored" (realist genres) as the
 * number 0, not null — all three WB components go to 0 together for those books.
 * We deliberately keep those zeros in correlations and spread:
 *   • It reproduces the calibrated fingerprint (Plot r≈0.91, Depth≈0.90,
 *     Ending≈0.89, and the three WB components trailing near r≈0.35). Dropping
 *     the zeros pushes WB up to ~0.8 and breaks that calibration.
 *   • The high WB spread (stdev≈3.2) is exactly the genre-mix effect the
 *     discrimination caption flags.
 * The one place WB zeros are excluded is the category radar, whose WB axis
 * averages only the books that actually scored Worldbuilding (see categoryRadar).
 */

import type { Book } from "./types";

/* ── Component taxonomy (keys match the API's nested `components`) ──────────── */

export const CATEGORIES = ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"] as const;
export type Category = (typeof CATEGORIES)[number];

export interface ComponentDef {
  /** Exact key under book.components[category]. */
  name: string;
  category: Category;
  /** Short code for tight axes (heatmap). */
  abbr: string;
}

/** The 14 components, in canonical category order. */
export const COMPONENTS: ComponentDef[] = [
  { name: "Plot", category: "Story", abbr: "Plot" },
  { name: "Entertainment", category: "Story", abbr: "Entn" },
  { name: "Action", category: "Story", abbr: "Actn" },
  { name: "Ending", category: "Story", abbr: "End" },
  { name: "Depth", category: "Character", abbr: "Dpth" },
  { name: "Emotional Impact", category: "Character", abbr: "Emo" },
  { name: "Motivations", category: "Character", abbr: "Motv" },
  { name: "Prose", category: "Aesthetics", abbr: "Pros" },
  { name: "Narration", category: "Aesthetics", abbr: "Narr" },
  { name: "Insights", category: "Theme", abbr: "Inst" },
  { name: "Thought-Provokingness", category: "Theme", abbr: "ThtP" },
  { name: "Depth2", category: "Worldbuilding", abbr: "Dpt2" },
  { name: "Integration", category: "Worldbuilding", abbr: "Intg" },
  { name: "Originality", category: "Worldbuilding", abbr: "Orig" },
];

/** Read one component score off a book; null when absent. */
export function componentValue(book: Book, comp: ComponentDef): number | null {
  const v = book.components?.[comp.category]?.[comp.name];
  return v == null ? null : v;
}

/* ── Math helpers ──────────────────────────────────────────────────────────── */

/** Pearson correlation. null when n<3 or either series has zero variance. */
export function pearson(xs: number[], ys: number[]): number | null {
  const n = xs.length;
  if (n < 3 || ys.length !== n) return null;
  let mx = 0, my = 0;
  for (let i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
  mx /= n; my /= n;
  let sxx = 0, syy = 0, sxy = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx, dy = ys[i] - my;
    sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
  }
  if (sxx === 0 || syy === 0) return null;
  return sxy / Math.sqrt(sxx * syy);
}

/** Sample standard deviation (÷ n−1). null when n<2. */
export function stdev(vals: number[]): number | null {
  const n = vals.length;
  if (n < 2) return null;
  const m = vals.reduce((a, b) => a + b, 0) / n;
  const v = vals.reduce((a, b) => a + (b - m) * (b - m), 0) / (n - 1);
  return Math.sqrt(v);
}

function mean(vals: number[]): number | null {
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

/* ── Header band ───────────────────────────────────────────────────────────── */

export interface HeaderStats {
  books: number;
  authors: number;
  genres: number;
  avgWA: number | null;
}

export function headerStats(books: Book[]): HeaderStats {
  return {
    books: books.length,
    authors: new Set(books.map((b) => b.author)).size,
    genres: new Set(books.map((b) => b.genre)).size,
    avgWA: mean(books.map((b) => b.wa)),
  };
}

export function libraryMeanWA(books: Book[]): number | null {
  return mean(books.map((b) => b.wa));
}

/* ── 1. Taste fingerprint: each component's correlation with WA ────────────── */

export interface CorrRow { comp: ComponentDef; r: number | null; n: number; }

export function tasteFingerprint(books: Book[]): CorrRow[] {
  const rows = COMPONENTS.map((comp) => {
    const xs: number[] = [], ys: number[] = [];
    for (const b of books) {
      const v = componentValue(b, comp);
      if (v == null) continue; // skip genuine nulls only (see policy note above)
      xs.push(v);
      ys.push(b.wa);
    }
    return { comp, r: pearson(xs, ys), n: xs.length };
  });
  // Descending by r; unknown (null) correlations sink to the bottom.
  return rows.sort((a, b) => (b.r ?? -Infinity) - (a.r ?? -Infinity));
}

/* ── 2. Discrimination profile: per-component spread ───────────────────────── */

export interface SdRow { comp: ComponentDef; sd: number | null; n: number; }

export function discriminationProfile(books: Book[]): SdRow[] {
  const rows = COMPONENTS.map((comp) => {
    const vals: number[] = [];
    for (const b of books) {
      const v = componentValue(b, comp);
      if (v != null) vals.push(v);
    }
    return { comp, sd: stdev(vals), n: vals.length };
  });
  return rows.sort((a, b) => (b.sd ?? -Infinity) - (a.sd ?? -Infinity));
}

/* ── 3. Category radar: mean weighted category average per category ─────────── */

export interface RadarPoint { category: Category; mean: number | null; n: number; }

/** Mean of one category's weighted average across books, applying the WB-sparse
 *  rule: Worldbuilding averages only the books that actually scored it (value > 0),
 *  because realist genres carry WB as 0 (absent), not a genuine zero rating. */
function categoryStat(books: Book[], category: Category): { mean: number | null; n: number } {
  const vals: number[] = [];
  for (const b of books) {
    const v = b.category_avgs?.[category];
    if (v == null) continue;
    if (category === "Worldbuilding" && v === 0) continue;
    vals.push(v);
  }
  return { mean: mean(vals), n: vals.length };
}

export function categoryRadar(books: Book[]): RadarPoint[] {
  return CATEGORIES.map((category) => ({ category, ...categoryStat(books, category) }));
}

/** Per-category mean weighted average (Story/Character/Aesthetics/Theme/Worldbuilding),
 *  WB averaged over only the books that scored it → null when none did. Drives the
 *  Reading tab's by-genre / by-author category columns. */
export function categoryAverages(books: Book[]): Record<Category, number | null> {
  const out = {} as Record<Category, number | null>;
  for (const category of CATEGORIES) out[category] = categoryStat(books, category).mean;
  return out;
}

/* ── 4. Genre affinity: volume vs. rating per genre ────────────────────────── */

export interface GenreAffinity { genre: string; count: number; avgWA: number; }

export function genreAffinity(books: Book[]): GenreAffinity[] {
  const byGenre = new Map<string, number[]>();
  for (const b of books) {
    const arr = byGenre.get(b.genre) ?? [];
    arr.push(b.wa);
    byGenre.set(b.genre, arr);
  }
  return [...byGenre.entries()]
    .map(([genre, was]) => ({ genre, count: was.length, avgWA: was.reduce((a, b) => a + b, 0) / was.length }))
    .sort((a, b) => b.count - a.count || b.avgWA - a.avgWA);
}

/** Distinct genres, most-read first — drives the filter chip row. */
export function genresByVolume(books: Book[]): string[] {
  return genreAffinity(books).map((g) => g.genre);
}

/* ── 5. Author leaderboard: favorites + reliability ────────────────────────── */

export interface AuthorStat {
  author: string;
  books: number;
  avgWA: number;
  /** stdev of this author's WAs; null when fewer than 2 books. */
  consistency: number | null;
  /** peak-weighted score: best work counts far above the tail (see favoriteScore). */
  favoriteScore: number;
}

/* Peak-weighted author score. Sort the author's WAs descending, weight the
   k-th best book by decay^k (best book counts fully; tail fades), then add a
   small capped depth bonus so a deep excellent catalog edges out a one-hit
   author. decay=0.6 ≈ "your top 2–3 books define you." */
const FAV_DECAY = 0.6;
const DEPTH_WEIGHT = 0.15;

export function favoriteScore(was: number[]): number {
  const sorted = [...was].sort((a, b) => b - a);
  let num = 0, den = 0;
  for (let k = 0; k < sorted.length; k++) {
    const w = Math.pow(FAV_DECAY, k);
    num += w * sorted[k];
    den += w;
  }
  const weighted = num / den;                       // decay-weighted mean
  const depthBonus = DEPTH_WEIGHT * Math.log2(sorted.length + 1);
  return weighted + depthBonus;
}

export function authorLeaderboard(books: Book[]): AuthorStat[] {
  const byAuthor = new Map<string, number[]>();
  for (const b of books) {
    const arr = byAuthor.get(b.author) ?? [];
    arr.push(b.wa);
    byAuthor.set(b.author, arr);
  }
  return [...byAuthor.entries()].map(([author, was]) => ({
    author,
    books: was.length,
    avgWA: was.reduce((a, b) => a + b, 0) / was.length,
    consistency: was.length >= 2 ? stdev(was) : null,
    favoriteScore: favoriteScore(was),
  }));
}

/* ── 6. Length sweet-spot: WA vs. word count ───────────────────────────────── */

export interface LengthPoint { title: string; words: number; wa: number; genre: string; }
export interface QuartileBin { label: string; loWords: number; hiWords: number; avgWA: number; n: number; }
export interface LengthSweetSpot { points: LengthPoint[]; bins: QuartileBin[]; }

export function lengthSweetSpot(books: Book[]): LengthSweetSpot {
  const points = books
    .filter((b) => b.words != null && b.words > 0)
    .map((b) => ({ title: b.title, words: b.words as number, wa: b.wa, genre: b.genre }));

  const sorted = [...points].sort((a, b) => a.words - b.words);
  const bins: QuartileBin[] = [];
  if (sorted.length >= 4) {
    for (let q = 0; q < 4; q++) {
      const lo = Math.floor((q * sorted.length) / 4);
      const hi = Math.floor(((q + 1) * sorted.length) / 4);
      const slice = sorted.slice(lo, hi);
      if (!slice.length) continue;
      bins.push({
        label: `Q${q + 1}`,
        loWords: slice[0].words,
        hiWords: slice[slice.length - 1].words,
        avgWA: slice.reduce((a, b) => a + b.wa, 0) / slice.length,
        n: slice.length,
      });
    }
  }
  return { points, bins };
}

/* ── 7. Component co-movement: pairwise correlation matrix ──────────────────── */

export interface CoMovement { labels: ComponentDef[]; matrix: (number | null)[][]; }

export function coMovement(books: Book[]): CoMovement {
  const labels = COMPONENTS;
  // Precompute each component's column once.
  const cols = labels.map((comp) => books.map((b) => componentValue(b, comp)));
  const matrix = labels.map((_, i) =>
    labels.map((__, j) => {
      if (i === j) return 1;
      const xs: number[] = [], ys: number[] = [];
      for (let k = 0; k < books.length; k++) {
        const a = cols[i][k], b = cols[j][k];
        if (a == null || b == null) continue; // pairwise skip on either null
        xs.push(a);
        ys.push(b);
      }
      return pearson(xs, ys);
    })
  );
  return { labels, matrix };
}
