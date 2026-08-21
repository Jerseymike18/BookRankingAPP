"use client";

/**
 * PREDICT JOBS — a prediction run that outlives the Predict page.
 *
 * A Discover → score → ground pass is minutes of LLM calls. It used to live in
 * `PredictFlow`'s own useState, which meant the App Router unmounted it the
 * moment the reader clicked any other tab: the fetches kept flying but their
 * results landed nowhere, and coming back showed an empty page. So the reader
 * had to sit and watch it.
 *
 * This provider is mounted in the ROOT LAYOUT, above `{children}`, so a route
 * change never unmounts it. All the async drivers live here; the Predict page is
 * now a view over this state. Navigate away mid-run and the run keeps going,
 * the nav shows a live progress pill, and a banner announces the finish.
 *
 * DURABILITY, stated honestly: this survives navigation WITHIN the site, which
 * is what it is for. It does not survive a full page reload or closing the tab —
 * an in-flight `fetch` dies with the document, and there is no server-side job to
 * poll (the run is client-orchestrated across several endpoints). A snapshot is
 * mirrored to sessionStorage so a reload restores the results already in hand
 * rather than throwing them away, and a run that was mid-flight when the page
 * went down comes back flagged `interrupted` rather than pretending it finished.
 *
 * sessionStorage, not localStorage, on purpose: it is scoped to the one tab and
 * dies with it, so a shared browser never hands one reader's predictions to the
 * next. `clearPredictJobs()` additionally wipes it on sign-out.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { PREDICT_RUNNERS } from "@/lib/predict-runners";
import { repredictRecommendation } from "@/lib/api";
import type { BookKind, Candidate, ScoredCandidate } from "@/lib/types";

/* Bounded-concurrency async pool: run `fn` over `items` with at most `limit`
   promises in flight at once. Used to grounded-refine several Discover
   candidates in parallel (each ~110s) instead of one-at-a-time, and to save a
   kept set in parallel, while capping concurrency to respect API rate limits.
   For large NON-interactive re-score jobs the Anthropic Message Batches API is
   the cheaper bulk path — not used here (this flow is interactive and small-N). */
async function mapPool<T>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<void>,
): Promise<void> {
  let next = 0;
  const run = async () => {
    while (true) {
      const i = next++;
      if (i >= items.length) break;
      await fn(items[i], i);
    }
  };
  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, run),
  );
}

/* Max grounded refines in flight at once. MEASURED, not assumed (2026-07-21):
   grounding 15 cold books took 333s at concurrency 8 but 386s at 15 — the wider
   fan-out is 16% SLOWER, not faster. Each grounded call is one Opus web_search
   turn, and firing more at once trips the server rate limiter harder: at 15,
   10/15 calls ate a ~60s Retry-After backoff (vs 8/15 at concurrency 8) and one
   badly-throttled straggler in the single big wave set the whole batch's
   wall-clock. So "one wave instead of two" is a false economy here — the one
   wave is throttle-bound. 8 is the measured-better default; the rate limiter
   starts biting around ~5-6 concurrent (the first few calls in any wave clear
   at the ~30s fast path, the rest queue behind backoff), so lower may be
   marginally better still, but 8 is the known-good value. Do NOT raise without
   re-measuring total wall — per-call latency is NOT independent of concurrency. */
const REFINE_CONCURRENCY = 8;

/* How many top candidates (by predicted WA) are grounded-refined automatically after
   scoring. The rest refine ON DEMAND (per card, or "Refine all"). Raised to 10 to match
   the opt-out save flow — the reader now keeps most candidates, so ground most of them
   up front rather than leaving them base-only. Still capped so a max-size Discover run
   (DISCOVER_MAX=15) doesn't fire a slow ~tens-of-seconds web_search for EVERY candidate;
   the eager batch runs bounded at REFINE_CONCURRENCY and progressively (scores stream in). */
export const EAGER_REFINE_K = 10;

/* Max recommendation saves in flight at once. Each /api/recommendations save is
   server-side ~2 LLM calls (series/ordinal lookup + rich house-style blurb,
   deferred from scoring so they're only paid for kept books), so saving a
   multi-book selection one-at-a-time was the slowest step in the flow. Bounded
   like REFINE_CONCURRENCY; the Anthropic SDK auto-retries 429s and each save is
   reported per-book, so a burst can't corrupt the batch. */
const SAVE_CONCURRENCY = 8;

/* ── Run state ───────────────────────────────────────────────────────────── */

export interface PredictRunState {
  /** The request textarea's draft. Held here so it survives navigation too. */
  request: string;
  requestLabel: string;
  candidates: Candidate[] | null;
  genNote: string;
  genSources: string[];
  genLoading: boolean;
  genError: string | null;
  scored: ScoredCandidate[];
  /** Which candidate is being scored right now, or null. */
  scoringIdx: number | null;
  scoringDone: boolean;
  /** Titles being grounded-refined right now. */
  refining: string[];
  /** Titles being re-predicted (no-cache) right now. */
  repredicting: string[];
  repredictErrors: Record<string, string>;
  removed: string[];
  saveResults: Record<string, string>;
  saving: boolean;
  saveProgress: { done: number; total: number };
  /** Set when a reload killed a run that was still in flight. */
  interrupted: string | null;
}

const EMPTY_RUN: PredictRunState = {
  request: "",
  requestLabel: "",
  candidates: null,
  genNote: "",
  genSources: [],
  genLoading: false,
  genError: null,
  scored: [],
  scoringIdx: null,
  scoringDone: false,
  refining: [],
  repredicting: [],
  repredictErrors: {},
  removed: [],
  saveResults: {},
  saving: false,
  saveProgress: { done: 0, total: 0 },
  interrupted: null,
};

type AllRuns = Record<BookKind, PredictRunState>;

const EMPTY_ALL: AllRuns = { fiction: EMPTY_RUN, nonfiction: EMPTY_RUN };

/** True while any long-running work is in flight for this kind. */
export function isRunBusy(r: PredictRunState): boolean {
  return (
    r.genLoading ||
    r.scoringIdx !== null ||
    r.refining.length > 0 ||
    r.repredicting.length > 0 ||
    r.saving
  );
}

/** The finished-run announcement the banner renders. One at a time — a newer
 *  completion replaces an unread older one rather than queueing. */
export interface PredictNotice {
  kind: BookKind;
  scored: number;
  failed: number;
  grounded: number;
  groundable: number;
  at: number;
}

interface PredictJobsValue {
  runs: AllRuns;
  /** Which kind the Predict page's toggle is showing. Held here rather than in
   *  the page so the finish banner can open the page on the kind that finished,
   *  and so the toggle choice survives navigation like everything else. */
  activeKind: BookKind;
  setActiveKind: (kind: BookKind) => void;
  notice: PredictNotice | null;
  dismissNotice: () => void;
  setRequest: (kind: BookKind, request: string) => void;
  dismissInterrupted: (kind: BookKind) => void;
  generate: (kind: BookKind) => void;
  score: (kind: BookKind) => void;
  refineOne: (kind: BookKind, title: string) => void;
  refineRemaining: (kind: BookKind, targets: ScoredCandidate[]) => void;
  repredictOne: (kind: BookKind, title: string) => void;
  save: (kind: BookKind, targets: ScoredCandidate[]) => void;
  removeBook: (kind: BookKind, title: string) => void;
  restoreAll: (kind: BookKind) => void;
}

const PredictJobsContext = createContext<PredictJobsValue | null>(null);

/* ── sessionStorage mirror ───────────────────────────────────────────────── */

const STORAGE_KEY = "trl:predict-jobs:v1";

/** Wipe any stored run. Called on sign-out so a shared browser never hands one
 *  reader's predictions to the next person to use the same tab. */
export function clearPredictJobs(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage disabled — nothing to clear */
  }
}

/** The persisted shape: the durable half of each run, plus whether it was still
 *  working when the snapshot was taken (so a reload can say so). */
function toSnapshot(runs: AllRuns) {
  const one = (r: PredictRunState) => ({
    request: r.request,
    requestLabel: r.requestLabel,
    candidates: r.candidates,
    genNote: r.genNote,
    genSources: r.genSources,
    scored: r.scored,
    scoringDone: r.scoringDone,
    removed: r.removed,
    saveResults: r.saveResults,
    repredictErrors: r.repredictErrors,
    busy: isRunBusy(r),
  });
  return { fiction: one(runs.fiction), nonfiction: one(runs.nonfiction) };
}

const INTERRUPTED_MSG =
  "This run was interrupted when the page reloaded — the books already scored are " +
  "below, but anything still in flight was lost. Score again to finish it.";

function fromSnapshot(raw: string): AllRuns | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const src = parsed as Record<string, Record<string, unknown> | undefined>;
  const one = (s: Record<string, unknown> | undefined): PredictRunState => {
    if (!s) return EMPTY_RUN;
    return {
      ...EMPTY_RUN,
      request: typeof s.request === "string" ? s.request : "",
      requestLabel: typeof s.requestLabel === "string" ? s.requestLabel : "",
      candidates: Array.isArray(s.candidates) ? (s.candidates as Candidate[]) : null,
      genNote: typeof s.genNote === "string" ? s.genNote : "",
      genSources: Array.isArray(s.genSources) ? (s.genSources as string[]) : [],
      scored: Array.isArray(s.scored) ? (s.scored as ScoredCandidate[]) : [],
      // A run cut off mid-scoring has no more scores coming, so the view must
      // treat what it has as final (that is what reveals the Save step) — the
      // `interrupted` note is what keeps that honest.
      scoringDone: s.busy === true ? Array.isArray(s.scored) && s.scored.length > 0
                                   : s.scoringDone === true,
      removed: Array.isArray(s.removed) ? (s.removed as string[]) : [],
      saveResults: (s.saveResults as Record<string, string>) ?? {},
      repredictErrors: (s.repredictErrors as Record<string, string>) ?? {},
      interrupted: s.busy === true ? INTERRUPTED_MSG : null,
    };
  };
  return { fiction: one(src.fiction), nonfiction: one(src.nonfiction) };
}

/* ── Provider ────────────────────────────────────────────────────────────── */

export function PredictJobsProvider({ children }: { children: React.ReactNode }) {
  const [runs, setRuns] = useState<AllRuns>(EMPTY_ALL);
  const [notice, setNotice] = useState<PredictNotice | null>(null);
  const [activeKind, setActiveKind] = useState<BookKind>("fiction");

  // Mirrors of state that an action handler must read SYNCHRONOUSLY at the
  // moment it starts, where a render-time closure would be stale. The in-flight
  // sets are the load-bearing ones: they are what stops a second click, or an
  // overlapping "Refine all", from firing a duplicate LLM call for a title
  // already being worked on.
  const refiningRef = useRef<Record<BookKind, Set<string>>>({
    fiction: new Set(), nonfiction: new Set(),
  });
  const repredictingRef = useRef<Record<BookKind, Set<string>>>({
    fiction: new Set(), nonfiction: new Set(),
  });
  const busyRef = useRef<Record<BookKind, boolean>>({ fiction: false, nonfiction: false });
  const runsRef = useRef<AllRuns>(EMPTY_ALL);
  useEffect(() => {
    runsRef.current = runs;
  }, [runs]);

  const patch = useCallback(
    (kind: BookKind, fn: (r: PredictRunState) => PredictRunState) => {
      setRuns((prev) => ({ ...prev, [kind]: fn(prev[kind]) }));
    },
    [],
  );

  /* ── Hydrate from the tab's snapshot, once, after mount ────────────────── */
  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    let raw: string | null = null;
    try {
      raw = window.sessionStorage.getItem(STORAGE_KEY);
    } catch {
      return; // storage disabled — run purely in memory
    }
    if (!raw) return;
    const restored = fromSnapshot(raw);
    if (restored) setRuns(restored);
  }, []);

  /* ── Mirror to the tab's snapshot on every change ──────────────────────── */
  useEffect(() => {
    if (!hydrated.current) return;
    if (runs === EMPTY_ALL) return; // nothing has happened yet
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(toSnapshot(runs)));
    } catch {
      /* over quota or disabled — persistence is a bonus, never a blocker */
    }
  }, [runs]);

  /* ── Completion announcement ───────────────────────────────────────────── */

  const announce = useCallback((n: Omit<PredictNotice, "at">) => {
    setNotice({ ...n, at: Date.now() });
  }, []);

  const dismissNotice = useCallback(() => setNotice(null), []);

  /* ── Actions ──────────────────────────────────────────────────────────── */

  const setRequest = useCallback(
    (kind: BookKind, request: string) => patch(kind, (r) => ({ ...r, request })),
    [patch],
  );

  const dismissInterrupted = useCallback(
    (kind: BookKind) => patch(kind, (r) => ({ ...r, interrupted: null })),
    [patch],
  );

  const removeBook = useCallback(
    (kind: BookKind, title: string) =>
      patch(kind, (r) =>
        r.removed.includes(title) ? r : { ...r, removed: [...r.removed, title] },
      ),
    [patch],
  );

  const restoreAll = useCallback(
    (kind: BookKind) => patch(kind, (r) => ({ ...r, removed: [] })),
    [patch],
  );

  /** Step 1 — ask the model for candidates. Clears the previous run for this kind. */
  const generate = useCallback(
    (kind: BookKind) => {
      const request = runsRef.current[kind].request.trim();
      // Refuse while this kind is still working. A run now outlives the page, so
      // an overlapping Discover is no longer self-limiting the way it was when
      // navigating away abandoned everything: the old scoring loop would keep
      // writing its results into the freshly cleared run. One run per kind.
      if (!request || isRunBusy(runsRef.current[kind]) || busyRef.current[kind]) return;
      patch(kind, (r) => ({
        ...EMPTY_RUN,
        request: r.request,
        genLoading: true,
      }));
      void (async () => {
        try {
          const result = await PREDICT_RUNNERS[kind].discover(request);
          patch(kind, (r) => ({
            ...r,
            genLoading: false,
            candidates: result.candidates,
            requestLabel: result.request,
            genNote: result.note ?? "",
            genSources: result.sources ?? [],
          }));
        } catch (e: unknown) {
          patch(kind, (r) => ({
            ...r,
            genLoading: false,
            genError: e instanceof Error ? e.message : "Generation failed.",
          }));
        }
      })();
    },
    [patch],
  );

  /** Progressive grounded (hybrid) refine: re-score the given candidates with the
   *  web-grounded upgrade and swap each in place as it lands, up to
   *  REFINE_CONCURRENCY at once. Skips any already grounded, currently refining,
   *  errored, or with no upgrade available — so it is safe to call repeatedly
   *  (eager top-K, per card, or "Refine all"). The memory scores are already on
   *  screen, so the reader can act immediately; grounded scores stream in and the
   *  list re-sorts. Resolves to the number newly grounded, which is what the
   *  completion notice counts. */
  const refineSet = useCallback(
    async (kind: BookKind, targets: ScoredCandidate[]): Promise<number> => {
      const refine = PREDICT_RUNNERS[kind].refine;
      if (!refine) return 0; // nonfiction has no grounded-refine path
      const inFlight = refiningRef.current[kind];
      const todo = targets.filter(
        (r) =>
          !r.error && r.hybrid_available && r.sourcing !== "hybrid" && !inFlight.has(r.title),
      );
      if (todo.length === 0) return 0;
      todo.forEach((r) => inFlight.add(r.title));
      patch(kind, (r) => ({
        ...r,
        refining: Array.from(new Set([...r.refining, ...todo.map((t) => t.title)])),
      }));
      let newlyGrounded = 0;
      await mapPool(todo, REFINE_CONCURRENCY, async (r) => {
        try {
          const g = await refine(r);
          if (g.sourcing === "hybrid") newlyGrounded += 1;
          patch(kind, (prev) => ({
            ...prev,
            scored: prev.scored.map((x) => (x.title === r.title ? { ...g } : x)),
          }));
        } catch {
          // keep the memory result if the grounded refine fails
        }
        inFlight.delete(r.title);
        patch(kind, (prev) => ({
          ...prev,
          refining: prev.refining.filter((t) => t !== r.title),
        }));
      });
      return newlyGrounded;
    },
    [patch],
  );

  /** Step 2 — score every candidate, then eagerly ground the top K. This whole
   *  function is the "run": it is what the nav pill tracks and what the finish
   *  banner announces, so the announcement fires exactly once, here, after the
   *  eager grounding settles — never from the gap between the two phases. */
  const score = useCallback(
    (kind: BookKind) => {
      const current = runsRef.current[kind];
      const candidates = current.candidates;
      if (!candidates || candidates.length === 0) return;
      // Same one-run-per-kind rule as generate(). busyRef is checked as well as
      // the state mirror because runsRef trails state by a commit, which a fast
      // double-click can beat.
      if (busyRef.current[kind] || isRunBusy(current)) return;
      busyRef.current[kind] = true;
      patch(kind, (r) => ({
        ...r,
        scored: [],
        scoringDone: false,
        scoringIdx: 0,
        refining: [],
        removed: [],
        saveResults: {},
        repredictErrors: {},
        interrupted: null,
      }));
      void (async () => {
        const results: ScoredCandidate[] = [];
        try {
          for (let i = 0; i < candidates.length; i++) {
            const c = candidates[i];
            patch(kind, (r) => ({ ...r, scoringIdx: i }));
            try {
              results.push(await PREDICT_RUNNERS[kind].score(c));
            } catch (e: unknown) {
              results.push({
                title: c.title, author: c.author, genre: c.genre ?? "",
                wa: 0, rank: 0, total: 0,
                n_genre: 0, n_author: 0, conf: "?",
                from_cache: false, words: null, series: "", series_number: null,
                blurb: "", keywords: "",
                components: {}, category_order: [],
                genre_auto_detected: false,
                error: e instanceof Error ? e.message : "Scoring failed",
              });
            }
            const snapshot = [...results];
            patch(kind, (r) => ({ ...r, scored: snapshot }));
          }
          patch(kind, (r) => ({ ...r, scoringIdx: null, scoringDone: true }));

          const ok = results.filter((r) => !r.error);
          // Eager pass (fiction only — nonfiction has no grounded upgrade):
          // grounded-refine only the top few by predicted score; the rest refine
          // on demand (per card, or "Refine all") so a large run doesn't fire N
          // slow calls.
          const alreadyGrounded = ok.filter((r) => r.sourcing === "hybrid").length;
          const groundable = ok.filter(
            (r) => r.hybrid_available || r.sourcing === "hybrid",
          ).length;
          let grounded = alreadyGrounded;
          if (PREDICT_RUNNERS[kind].refine) {
            const eager = [...ok]
              .sort((a, b) => PREDICT_RUNNERS[kind].primaryScore(b) - PREDICT_RUNNERS[kind].primaryScore(a))
              .slice(0, EAGER_REFINE_K);
            grounded += await refineSet(kind, eager);
          }
          announce({
            kind,
            scored: ok.length,
            failed: results.length - ok.length,
            grounded,
            groundable,
          });
        } finally {
          busyRef.current[kind] = false;
          patch(kind, (r) => ({ ...r, scoringIdx: null, scoringDone: true }));
        }
      })();
    },
    [patch, refineSet, announce],
  );

  /** Refine one candidate on demand. No completion banner — the reader asked for
   *  one book and the card itself shows the answer. */
  const refineOne = useCallback(
    (kind: BookKind, title: string) => {
      const r = runsRef.current[kind].scored.find((x) => x.title === title);
      if (r) void refineSet(kind, [r]);
    },
    [refineSet],
  );

  /** "Refine all remaining" — a bulk pass, so it DOES announce when it lands:
   *  it is long enough that the reader is expected to go elsewhere. */
  const refineRemaining = useCallback(
    (kind: BookKind, targets: ScoredCandidate[]) => {
      void (async () => {
        const before = runsRef.current[kind].scored;
        const alreadyGrounded = before.filter(
          (r) => !r.error && r.sourcing === "hybrid",
        ).length;
        const groundable = before.filter(
          (r) => !r.error && (r.hybrid_available || r.sourcing === "hybrid"),
        ).length;
        const newly = await refineSet(kind, targets);
        if (newly === 0) return; // nothing actually ran — don't announce a no-op
        announce({
          kind,
          scored: before.filter((r) => !r.error).length,
          failed: before.filter((r) => !!r.error).length,
          grounded: alreadyGrounded + newly,
          groundable,
        });
      })();
    },
    [refineSet, announce],
  );

  /** Re-predict ONE card: a genuinely fresh (no-cache) look at this book.
   *
   *  The saved-book step is the subtle part. A card can already be saved (the
   *  reader hit Save, or it's a book they typed that's on their TBR). Re-scoring
   *  only the card would leave the STORED prediction stale — the card and the
   *  read-queue would disagree about the same book. So for a saved book we follow
   *  with the re-predict endpoint, which is FREE here: the forced call above just
   *  overwrote that book's research-cache entry, so the (cache-first) endpoint
   *  re-corrects and persists exactly the vector now on screen. One LLM call total. */
  const repredictOne = useCallback(
    (kind: BookKind, title: string) => {
      const repredict = PREDICT_RUNNERS[kind].repredict;
      const r = runsRef.current[kind].scored.find((x) => x.title === title);
      const inFlight = repredictingRef.current[kind];
      if (!repredict || !r || inFlight.has(title)) return;
      inFlight.add(title);
      patch(kind, (prev) => ({
        ...prev,
        repredicting: [...prev.repredicting, title],
        repredictErrors: Object.fromEntries(
          Object.entries(prev.repredictErrors).filter(([k]) => k !== title),
        ),
      }));
      void (async () => {
        try {
          const fresh = await repredict(r);
          patch(kind, (prev) => ({
            ...prev,
            scored: prev.scored.map((x) => (x.title === title ? { ...fresh } : x)),
          }));
          const saved = runsRef.current[kind].saveResults[title];
          if (saved && !saved.startsWith("Error")) {
            try {
              const { report } = await repredictRecommendation(title);
              patch(kind, (prev) => ({
                ...prev,
                saveResults: {
                  ...prev.saveResults,
                  [title]: report.changed
                    ? `Saved · re-predicted, WA ${report.old_wa?.toFixed(2) ?? "—"} → ${report.new_wa.toFixed(2)}`
                    : "Saved · re-predicted, no change",
                },
              }));
            } catch {
              // The card is fresh but the stored row is not — say so rather than
              // letting the two silently disagree.
              patch(kind, (prev) => ({
                ...prev,
                saveResults: {
                  ...prev.saveResults,
                  [title]:
                    "Error: re-predicted here, but your saved copy could not be updated.",
                },
              }));
            }
          }
        } catch (e) {
          // Keep the existing (good) prediction on screen and report the failure
          // beside it. Writing `error` onto the card would move it into the failed
          // bucket and throw away a result the reader already has.
          patch(kind, (prev) => ({
            ...prev,
            repredictErrors: {
              ...prev.repredictErrors,
              [title]: e instanceof Error ? e.message : "Re-predict failed.",
            },
          }));
        } finally {
          inFlight.delete(title);
          patch(kind, (prev) => ({
            ...prev,
            repredicting: prev.repredicting.filter((t) => t !== title),
          }));
        }
      })();
    },
    [patch],
  );

  /** Step 3 — save the kept, not-yet-saved books with bounded concurrency instead
   *  of one-at-a-time; each save costs ~2 server-side LLM calls, so a sequential
   *  loop stacked that cost linearly. `targets` comes from the view, which already
   *  computes the opt-out kept set. */
  const save = useCallback(
    (kind: BookKind, targets: ScoredCandidate[]) => {
      if (targets.length === 0 || runsRef.current[kind].saving) return;
      patch(kind, (r) => ({
        ...r,
        saving: true,
        saveProgress: { done: 0, total: targets.length },
      }));
      void (async () => {
        // Distinct titles write distinct keys of `newResults`, so the concurrent
        // writes don't race (single-threaded event loop, one key each).
        const newResults: Record<string, string> = {};
        await mapPool(targets, SAVE_CONCURRENCY, async (r) => {
          try {
            const res = await PREDICT_RUNNERS[kind].save(r);
            newResults[r.title] = res.message || "Saved.";
          } catch (e: unknown) {
            newResults[r.title] = `Error: ${e instanceof Error ? e.message : "Failed"}`;
          }
          // Count every finished save, successful or not — the bar tracks work
          // completed, and the per-card message carries the outcome.
          patch(kind, (prev) => ({
            ...prev,
            saveProgress: { ...prev.saveProgress, done: prev.saveProgress.done + 1 },
          }));
        });
        // Merge (don't replace) so an earlier batch's ✓ results survive a second save.
        patch(kind, (prev) => ({
          ...prev,
          saving: false,
          saveResults: { ...prev.saveResults, ...newResults },
        }));
      })();
    },
    [patch],
  );

  const value = useMemo<PredictJobsValue>(
    () => ({
      runs,
      activeKind,
      setActiveKind,
      notice,
      dismissNotice,
      setRequest,
      dismissInterrupted,
      generate,
      score,
      refineOne,
      refineRemaining,
      repredictOne,
      save,
      removeBook,
      restoreAll,
    }),
    [
      runs, activeKind, notice, dismissNotice, setRequest, dismissInterrupted,
      generate, score, refineOne, refineRemaining, repredictOne, save, removeBook,
      restoreAll,
    ],
  );

  return (
    <PredictJobsContext.Provider value={value}>{children}</PredictJobsContext.Provider>
  );
}

/** The provider is mounted in the root layout, so this is always available. */
export function usePredictJobs(): PredictJobsValue {
  const ctx = useContext(PredictJobsContext);
  if (!ctx) throw new Error("usePredictJobs must be used inside <PredictJobsProvider>");
  return ctx;
}

/** Non-throwing variant for chrome that renders on every route (the nav pill,
 *  the banner) and must not explode if it is ever rendered outside the provider. */
export function useOptionalPredictJobs(): PredictJobsValue | null {
  return useContext(PredictJobsContext);
}
