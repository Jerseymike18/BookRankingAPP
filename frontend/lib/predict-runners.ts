/**
 * PREDICT RUNNERS — the API-calling half of the Predict flow, per book kind.
 *
 * Split out of PredictClient so the background job provider (lib/predict-jobs.tsx)
 * can drive a prediction run without needing anything from the page. The Predict
 * page's own config still owns everything PRESENTATIONAL (labels, placeholder
 * prose, which badge to show); what lives here is only the four/five calls a run
 * actually makes, which depend on nothing but the kind.
 *
 * That split is what lets a run outlive the page: the provider sits in the root
 * layout, so navigating to another tab unmounts the view but not the work.
 *
 * Nothing here reimplements prediction math — every function is a thin wrapper
 * over the existing endpoints in lib/api.ts.
 */

import {
  predictResearch,
  discoverCandidates,
  saveRecommendation,
  predictNonfiction,
  saveNonfictionRecommendation,
  discoverNonfictionCandidates,
} from "@/lib/api";
import type {
  Candidate,
  ScoredCandidate,
  NonfictionPrediction,
  BookKind,
} from "@/lib/types";

/** Flatten a scored book's grouped-by-category components into the flat score map
 *  the save endpoints expect. Shared by the fiction and nonfiction save paths. */
export function flattenScores(
  components: ScoredCandidate["components"],
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const cat of Object.values(components)) {
    for (const [c, v] of Object.entries(cat)) if (v != null) out[c] = v;
  }
  return out;
}

/** Adapt a nonfiction prediction into the fiction-shaped ScoredCandidate the shared
 *  card consumes. Nonfiction carries no conformal interval and no hybrid upgrade,
 *  so those fields stay absent and the card degrades gracefully (no interval row,
 *  no Refine affordance). */
function nfToScored(p: NonfictionPrediction): ScoredCandidate {
  return {
    title: p.title, author: p.author, genre: p.genre,
    wa: p.wa, total_average: p.total_average,
    rank: p.rank, total: p.total,
    n_genre: p.n_genre ?? 0, n_author: p.n_author ?? 0,
    conf: p.confidence, from_cache: p.from_cache ?? false,
    words: p.words ?? null, series: p.series ?? "", series_number: p.series_number ?? null,
    blurb: p.blurb ?? "", keywords: p.keywords ?? "",
    components: p.components, category_order: p.category_order,
    genre_auto_detected: p.genre_auto_detected ?? false,
    sourcing: p.sourcing ?? "memory",
    hybrid_available: p.hybrid_available ?? false,
  };
}

export interface PredictRunner {
  /** The score a card leads with, the ranked list sorts by, and the eager
   *  grounded-refine pass picks its top-K from: WA (fiction), Total Average
   *  (nonfiction). It lives here rather than in the page's presentation config
   *  because the background run needs it with no page mounted. */
  primaryScore: (r: ScoredCandidate) => number;
  discover: (request: string) => Promise<{
    candidates: Candidate[]; request: string; note?: string; sources?: string[];
  }>;
  score: (c: Candidate) => Promise<ScoredCandidate>;
  save: (r: ScoredCandidate) => Promise<{ message?: string }>;
  /** Grounded (hybrid) re-score. Fiction only — when omitted the eager pass, the
   *  per-card Refine button, and the refine banner all disable themselves. */
  refine?: (r: ScoredCandidate) => Promise<ScoredCandidate>;
  /** No-cache re-score ("Re-predict"). Fiction only — omitted for nonfiction, whose
   *  re-predict lives on its read-queue instead, where it can persist the result. */
  repredict?: (r: ScoredCandidate) => Promise<ScoredCandidate>;
}

const FICTION_RUNNER: PredictRunner = {
  primaryScore: (r) => r.wa,
  discover: (request) => discoverCandidates(request),
  score: (c) => predictResearch(c.title, c.author, c.genre ?? undefined),
  refine: (r) => predictResearch(r.title, r.author, r.genre, true),
  // Re-predict = the no-cache refresh. Distinct from Refine: Refine upgrades
  // memory scores to grounded ones and is only offered until a book IS grounded;
  // this takes a genuinely fresh look at any book, grounded or not. Fiction only —
  // the nonfiction runner leaves it undefined so the button never appears there
  // (nonfiction's own re-predict lives on its read-queue, where it can persist).
  repredict: (r) => predictResearch(r.title, r.author, r.genre, true, true),
  save: (r) =>
    saveRecommendation({
      title: r.title, genre: r.genre, author: r.author,
      scores: flattenScores(r.components),
      words: r.words ?? undefined,
      series: r.series || undefined,
      series_number: r.series_number ?? undefined,
      blurb: r.blurb || undefined,
      keywords: r.keywords || undefined,
    }),
};

const NONFICTION_RUNNER: PredictRunner = {
  primaryScore: (r) => r.total_average ?? 0,
  discover: async (request) => {
    const r = await discoverNonfictionCandidates(request);
    return {
      candidates: r.candidates.map((c) => ({
        title: c.title,
        author: c.author,
        genre: c.genre ?? "Nonfiction",
        cached: c.cached ?? false,
        series: c.series ?? null,
        series_number: c.series_number ?? null,
        requested: c.requested ?? false,
      })),
      request: r.request,
      note: r.note,
      sources: r.sources ?? [],
    };
  },
  score: async (c) =>
    nfToScored(await predictNonfiction(c.title, c.author, c.genre ?? undefined)),
  // no refine — nonfiction has no grounded-refine path
  save: (r) =>
    saveNonfictionRecommendation({
      title: r.title, author: r.author, genre: r.genre,
      scores: flattenScores(r.components),
      words: r.words ?? undefined,
      series: r.series || undefined,
      series_number: r.series_number ?? undefined,
      blurb: r.blurb || undefined,
      keywords: r.keywords || undefined,
    }),
};

export const PREDICT_RUNNERS: Record<BookKind, PredictRunner> = {
  fiction: FICTION_RUNNER,
  nonfiction: NONFICTION_RUNNER,
};
