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
import {
  repredictRecommendation,
  repredictNonfictionRecommendation,
  isCancelled,
} from "@/lib/api";
import type {
  GenreRecommendResponse,
  BookKind,
  Candidate,
  ScoredCandidate,
  RepredictOneReport,
} from "@/lib/types";

/* The read-queue's per-book Re-predict, by kind. Deliberately NOT part of
   PredictRunner: that describes the Discover flow's calls, and these two are a
   different operation on a different page (and, for nonfiction, a materially
   different one — it forces a fresh Opus call because a nonfiction book's scores
   don't depend on the library, so there is no cheap cached path). */
const SAVED_REPREDICT: Record<
  BookKind,
  (title: string, signal?: AbortSignal) => Promise<{ report: RepredictOneReport }>
> = {
  fiction: repredictRecommendation,
  nonfiction: repredictNonfictionRecommendation,
};

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
  /** Set when the reader pressed Stop. Distinct from `interrupted`: that is
   *  something that happened TO the run, this is something they chose. */
  cancelled: string | null;
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
  cancelled: null,
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

/** The finished-work announcement the banner + OS notification render. One at a
 *  time — a newer completion replaces an unread older one rather than queueing.
 *
 *  A union rather than two channels: both job types finish the same way (tell the
 *  reader wherever they are), so they share one banner, one notification path,
 *  and one "have I already announced this" guard. */
export type PredictNotice =
  | {
      type: "run";
      kind: BookKind;
      at: number;
      scored: number;
      failed: number;
      grounded: number;
      groundable: number;
    }
  | {
      type: "repredict";
      kind: BookKind;
      at: number;
      title: string;
      report: RepredictOneReport;
    };

type NoticeInput =
  | { type: "run"; kind: BookKind; scored: number; failed: number; grounded: number; groundable: number }
  | { type: "repredict"; kind: BookKind; title: string; report: RepredictOneReport };

/** One saved book being re-predicted from the read-queue. Keyed by kind+title so
 *  several can run at once and each card watches only its own. */
export interface QueueRepredictJob {
  kind: BookKind;
  title: string;
  status: "running" | "done" | "error" | "cancelled";
  report: RepredictOneReport | null;
  error: string | null;
  /** Bumped when the job settles, so a card can refresh the route exactly once. */
  at: number;
}

export function queueRepredictKey(kind: BookKind, title: string): string {
  return `${kind}:${title}`;
}

/** Everything the genre-prediction page remembers.
 *
 *  Held in this provider, which is mounted ABOVE the router in the root layout —
 *  the same reason `activeKind` lives here. "Recommend genres → hand a request
 *  to book prediction → come back" is the normal path, and that is a real
 *  navigation now that the two are separate pages, so page-local state would
 *  discard a recommendation it had just spent a call to produce.
 *
 *  It IS mirrored to the tab snapshot, so a recommendation survives a reload as
 *  well as a navigation. Two consequences that are easy to get wrong:
 *
 *  - It rides the SAME `STORAGE_KEY` as the runs, so `clearPredictJobs` already
 *    wipes it on sign-out. That is required, not incidental: this is derived
 *    wholly from one reader's library, and a shared browser must never hand it
 *    to whoever signs in next.
 *  - `loading` and `error` are NOT persisted. A reload kills the in-flight
 *    fetch, so restoring `loading: true` would leave a spinner nothing will ever
 *    finish; and an error reports a moment that a reload has already ended —
 *    the same reasoning that clears the Discover error banner on arrival. A
 *    reload that lands mid-call restores `interrupted` instead, which says so. */
export interface GenreTabState {
  focus: string;
  loading: boolean;
  error: string | null;
  result: GenreRecommendResponse | null;
  showEvidence: boolean;
  /** Set on restore when the snapshot was taken mid-call. Without it the reader
   *  reloads into an idle button and no result, with nothing saying why. */
  interrupted: string | null;
}

export const EMPTY_GENRE_TAB: GenreTabState = {
  focus: "",
  loading: false,
  error: null,
  result: null,
  showEvidence: false,
  interrupted: null,
};

const GENRE_INTERRUPTED_MSG =
  "That recommendation was still being written when the page reloaded, so it was " +
  "lost. Press Recommend genres again — nothing was saved either way.";

interface PredictJobsValue {
  runs: AllRuns;
  /** Saved-book re-predictions in flight or recently finished, by
   *  queueRepredictKey(kind, title). */
  queueRepredicts: Record<string, QueueRepredictJob>;
  startQueueRepredict: (kind: BookKind, title: string) => void;
  clearQueueRepredict: (kind: BookKind, title: string) => void;
  cancelQueueRepredict: (kind: BookKind, title: string) => void;
  /** Stop everything in flight for this kind's Predict run. */
  cancelRun: (kind: BookKind) => void;
  dismissCancelled: (kind: BookKind) => void;
  /** Which kind the Predict page's toggle is showing. Held here rather than in
   *  the page so the finish banner can open the page on the kind that finished,
   *  and so the toggle choice survives navigation like everything else. */
  activeKind: BookKind;
  setActiveKind: (kind: BookKind) => void;
  notice: PredictNotice | null;
  dismissNotice: () => void;
  setRequest: (kind: BookKind, request: string) => void;
  /** Genre-prediction page state — survives navigating to book prediction and
   *  back. See GenreTabState. */
  genreTab: GenreTabState;
  patchGenreTab: (p: Partial<GenreTabState>) => void;
  /** Set by the genre page just before it pushes to /predict, consumed once by
   *  the book page on mount. Without it the reader arrives at a request box
   *  that filled itself silently, which reads as nothing having happened. */
  requestFocusOnArrival: () => void;
  consumeArrivalFocus: () => boolean;
  dismissInterrupted: (kind: BookKind) => void;
  dismissGenError: (kind: BookKind) => void;
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
let signedOut = false;

export function clearPredictJobs(): void {
  // Latch first. Sign-out awaits the Supabase call before navigating, and a run
  // (or a genre recommendation) that lands inside that window would setState →
  // fire the mirror effect → rewrite the snapshot we just deleted. The flag
  // lives until the next full page load, which sign-out always performs.
  signedOut = true;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage disabled — nothing to clear */
  }
}

/** The persisted shape: the durable half of each run, plus whether it was still
 *  working when the snapshot was taken (so a reload can say so). */
function toSnapshot(runs: AllRuns, genreTab: GenreTabState) {
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
  return {
    fiction: one(runs.fiction),
    nonfiction: one(runs.nonfiction),
    // `busy` rather than `loading`, matching the runs above: the flag records
    // that a call was in flight when the snapshot was taken, which the restore
    // turns into a message rather than back into a spinner.
    genreTab: {
      focus: genreTab.focus,
      result: genreTab.result,
      showEvidence: genreTab.showEvidence,
      busy: genreTab.loading,
    },
  };
}

const INTERRUPTED_MSG =
  "This run was interrupted when the page reloaded — the books already scored are " +
  "below, but anything still in flight was lost. Score again to finish it.";

/** Is this stored blob still a usable recommendation payload? Checks only the
 *  fields the page actually renders off — enough to keep a corrupt or
 *  stale-shaped snapshot from crashing the load. */
function _isGenreResult(v: unknown): v is GenreRecommendResponse {
  if (!v || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  return Array.isArray(r.genres) && Array.isArray(r.types) && Array.isArray(r.evidence);
}

function fromSnapshot(raw: string): { runs: AllRuns; genreTab: GenreTabState } | null {
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
      cancelled: null,
    };
  };
  // Tolerant by design: a snapshot written before genre prediction existed simply
  // has no `genreTab` key, and restores as empty rather than failing the whole
  // hydrate. That is why the STORAGE_KEY did not need a version bump — bumping it
  // would have thrown away the in-flight runs of anyone mid-scoring at deploy.
  const g = src.genreTab;
  const genreTab: GenreTabState = !g
    ? EMPTY_GENRE_TAB
    : {
        ...EMPTY_GENRE_TAB,
        focus: typeof g.focus === "string" ? g.focus : "",
        // Shape-checked, not just null-checked — the runs above validate their
        // arrays for the same reason. A stored `result` that is a string or a
        // half-written object would otherwise reach `result.genres.map` and take
        // the page down on load, with no way to recover but clearing storage.
        result: _isGenreResult(g.result) ? g.result : null,
        showEvidence: g.showEvidence === true,
        interrupted: g.busy === true ? GENRE_INTERRUPTED_MSG : null,
      };
  return { runs: { fiction: one(src.fiction), nonfiction: one(src.nonfiction) }, genreTab };
}

/* ── Provider ────────────────────────────────────────────────────────────── */

export function PredictJobsProvider({ children }: { children: React.ReactNode }) {
  const [runs, setRuns] = useState<AllRuns>(EMPTY_ALL);
  const [notice, setNotice] = useState<PredictNotice | null>(null);
  const [activeKind, setActiveKind] = useState<BookKind>("fiction");
  const [genreTab, setGenreTab] = useState<GenreTabState>(EMPTY_GENRE_TAB);
  // A ref, not state: consuming it must not re-render, and nothing renders from it.
  const arrivalFocus = useRef(false);
  const [queueRepredicts, setQueueRepredicts] = useState<
    Record<string, QueueRepredictJob>
  >({});

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
  // Synchronous mirror of queueRepredicts: the double-click guard has to read it
  // in the click handler, before any render has committed. Every writer updates
  // both, so the two cannot drift.
  const queueRepredictsRef = useRef<Record<string, QueueRepredictJob>>({});

  // One AbortController per kind covers that kind's whole Predict run —
  // discover, the scoring loop, the grounded refine, the save pass. One Stop
  // that halts everything for the run the reader is looking at is clearer than
  // per-phase controls, and the one-run-per-kind rule means nothing unrelated is
  // ever caught by it. Saved-book re-predictions get their own controller each,
  // since they are independent jobs on a different page.
  const runAbort = useRef<Record<BookKind, AbortController | null>>({
    fiction: null, nonfiction: null,
  });
  const repredictAbort = useRef<Record<string, AbortController>>({});

  /** The signal for work starting now. Reuses the kind's live controller so a
   *  save started after a completed run is still covered by the same Stop, and
   *  mints a fresh one once the previous was aborted. */
  const runSignal = useCallback((kind: BookKind): AbortSignal => {
    const cur = runAbort.current[kind];
    if (cur && !cur.signal.aborted) return cur.signal;
    const next = new AbortController();
    runAbort.current[kind] = next;
    return next.signal;
  }, []);
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
    if (restored) {
      setRuns(restored.runs);
      setGenreTab(restored.genreTab);
    }
  }, []);

  /* ── Mirror to the tab's snapshot on every change ──────────────────────── */
  useEffect(() => {
    if (!hydrated.current) return;
    if (signedOut) return; // never re-create a snapshot after sign-out cleared it
    // Nothing has happened yet in EITHER half. Checking only `runs` would skip
    // the write for a reader who has asked for genres but never scored a book —
    // which is exactly the first-use path for the genre page.
    if (runs === EMPTY_ALL && genreTab === EMPTY_GENRE_TAB) return;
    try {
      window.sessionStorage.setItem(
        STORAGE_KEY, JSON.stringify(toSnapshot(runs, genreTab)));
    } catch {
      /* over quota or disabled — persistence is a bonus, never a blocker */
    }
  }, [runs, genreTab]);

  /* ── Completion announcement ───────────────────────────────────────────── */

  const announce = useCallback((n: NoticeInput) => {
    setNotice({ ...n, at: Date.now() } as PredictNotice);
  }, []);

  const dismissNotice = useCallback(() => setNotice(null), []);

  /* ── Actions ──────────────────────────────────────────────────────────── */

  /**
   * Stop this kind's run.
   *
   * Precise about what this can and cannot do, because the difference is money:
   *   - Calls NOT YET STARTED never happen. On a 12-book scoring run stopped at
   *     book 4, that is 8 LLM calls saved. This is the real effect.
   *   - The request IN FLIGHT is aborted at the browser's end, so the UI stops
   *     waiting — but the handler on the other side runs to completion and its
   *     Anthropic call is already paid for. Stopping is not a refund.
   *   - Everything already scored STAYS. A stopped run is a partial result, not
   *     a discarded one, so the reader keeps what they paid for and can save it.
   */
  const cancelRun = useCallback(
    (kind: BookKind) => {
      const c = runAbort.current[kind];
      if (!c || c.signal.aborted) return;
      c.abort();
      runAbort.current[kind] = null;
      busyRef.current[kind] = false;
      refiningRef.current[kind].clear();
      patch(kind, (r) => ({
        ...r,
        genLoading: false,
        scoringIdx: null,
        // Whatever was scored before the stop is a real result the reader can
        // still act on, so reveal the save step rather than hiding it.
        scoringDone: r.scored.length > 0 ? true : r.scoringDone,
        refining: [],
        saving: false,
        cancelled:
          "Stopped. Nothing further was requested — anything already scored is " +
          "below and can still be saved. A call that was already in flight may " +
          "still finish on the server.",
      }));
    },
    [patch],
  );

  const dismissCancelled = useCallback(
    (kind: BookKind) => patch(kind, (r) => ({ ...r, cancelled: null })),
    [patch],
  );

  const setRequest = useCallback(
    (kind: BookKind, request: string) => patch(kind, (r) => ({ ...r, request })),
    [patch],
  );

  const dismissInterrupted = useCallback(
    (kind: BookKind) => patch(kind, (r) => ({ ...r, interrupted: null })),
    [patch],
  );

  /**
   * Drop a stale "couldn't generate candidates" banner.
   *
   * This exists because moving the run into this provider changed the lifetime
   * of errors as well as results, and those two want opposite treatment. A run's
   * RESULTS should survive navigation — that is the whole feature. An error
   * banner should not: it reports a moment, usually an external and transient
   * one, and the page has no way to know the moment has passed. The Predict view
   * clears this on mount (restoring what unmounting used to do for free) and the
   * banner carries a dismiss, so a failure can no longer outlive its cause and
   * be read as current.
   */
  const dismissGenError = useCallback(
    (kind: BookKind) => patch(kind, (r) => (r.genError === null ? r : { ...r, genError: null })),
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
      const signal = runSignal(kind);
      patch(kind, (r) => ({
        ...EMPTY_RUN,
        request: r.request,
        genLoading: true,
      }));
      void (async () => {
        try {
          const result = await PREDICT_RUNNERS[kind].discover(request, signal);
          patch(kind, (r) => ({
            ...r,
            genLoading: false,
            candidates: result.candidates,
            requestLabel: result.request,
            genNote: result.note ?? "",
            genSources: result.sources ?? [],
          }));
        } catch (e: unknown) {
          // A cancel is not a failure: cancelRun has already written the note.
          if (isCancelled(e)) return;
          patch(kind, (r) => ({
            ...r,
            genLoading: false,
            genError: e instanceof Error ? e.message : "Generation failed.",
          }));
        }
      })();
    },
    [patch, runSignal],
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
      const signal = runSignal(kind);
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
        // Checked per item, not just once: mapPool holds items in a queue behind
        // REFINE_CONCURRENCY, so a Stop must prevent the queued ones from ever
        // being requested. That is the whole saving.
        if (signal.aborted) {
          inFlight.delete(r.title);
          return;
        }
        try {
          const g = await refine(r, signal);
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
    [patch, runSignal],
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
      const signal = runSignal(kind);
      patch(kind, (r) => ({
        ...r,
        scored: [],
        scoringDone: false,
        scoringIdx: 0,
        cancelled: null,
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
            if (signal.aborted) return;   // stop BEFORE spending the next call
            const c = candidates[i];
            patch(kind, (r) => ({ ...r, scoringIdx: i }));
            try {
              results.push(await PREDICT_RUNNERS[kind].score(c, signal));
            } catch (e: unknown) {
              // A cancelled call is not a failed book — recording it as an error
              // card would put a book the reader stopped into the failed bucket.
              if (isCancelled(e)) return;
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
            type: "run",
            kind,
            scored: ok.length,
            failed: results.length - ok.length,
            grounded,
            groundable,
          });
        } finally {
          busyRef.current[kind] = false;
          // A cancelled run keeps the note cancelRun wrote and its partial
          // results; scoringDone is already true there, set by the same call.
          if (!signal.aborted) {
            patch(kind, (r) => ({ ...r, scoringIdx: null, scoringDone: true }));
          }
        }
      })();
    },
    [patch, refineSet, announce, runSignal],
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
          type: "run",
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
      const signal = runSignal(kind);
      void (async () => {
        try {
          const fresh = await repredict(r, signal);
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
          if (isCancelled(e)) return;   // cancelRun already said so
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
    [patch, runSignal],
  );

  /** Step 3 — save the kept, not-yet-saved books with bounded concurrency instead
   *  of one-at-a-time; each save costs ~2 server-side LLM calls, so a sequential
   *  loop stacked that cost linearly. `targets` comes from the view, which already
   *  computes the opt-out kept set. */
  const save = useCallback(
    (kind: BookKind, targets: ScoredCandidate[]) => {
      if (targets.length === 0 || runsRef.current[kind].saving) return;
      const signal = runSignal(kind);
      patch(kind, (r) => ({
        ...r,
        saving: true,
        cancelled: null,
        saveProgress: { done: 0, total: targets.length },
      }));
      void (async () => {
        // Distinct titles write distinct keys of `newResults`, so the concurrent
        // writes don't race (single-threaded event loop, one key each).
        const newResults: Record<string, string> = {};
        await mapPool(targets, SAVE_CONCURRENCY, async (r) => {
          if (signal.aborted) return;   // never start a queued save after Stop
          try {
            const res = await PREDICT_RUNNERS[kind].save(r, signal);
            newResults[r.title] = res.message || "Saved.";
          } catch (e: unknown) {
            // Leave a cancelled book with NO result rather than an error one, so
            // it stays in `savable` and the reader can simply press Save again.
            if (isCancelled(e)) return;
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
    [patch, runSignal],
  );

  /** Re-predict ONE saved book from the read-queue, in the background.
   *
   *  This is the read-queue's own operation, not the Predict page's: it hits
   *  POST /api/{nonfiction/}recommendations/{title}/repredict, which re-predicts
   *  the stored row against the library as it stands now and PERSISTS the result.
   *  It is synchronous server-side and can run past a minute on a book that has
   *  never been web-grounded, which is exactly why it belongs here rather than in
   *  the card: the card unmounts the moment the reader collapses it or leaves the
   *  page, and the answer would have had nowhere to land.
   *
   *  In-flight jobs are keyed by kind+title, so several books can re-predict at
   *  once and a second click on the same book is a no-op rather than a second
   *  paid call. */
  const startQueueRepredict = useCallback(
    (kind: BookKind, title: string) => {
      const key = queueRepredictKey(kind, title);
      if (queueRepredictsRef.current[key]?.status === "running") return;
      const patchJob = (job: QueueRepredictJob) =>
        setQueueRepredicts((prev) => {
          const next = { ...prev, [key]: job };
          queueRepredictsRef.current = next;
          return next;
        });
      const controller = new AbortController();
      repredictAbort.current[key] = controller;
      patchJob({ kind, title, status: "running", report: null, error: null, at: 0 });
      void (async () => {
        try {
          const { report } = await SAVED_REPREDICT[kind](title, controller.signal);
          patchJob({
            kind, title, status: "done", report, error: null, at: Date.now(),
          });
          announce({ type: "repredict", kind, title, report });
        } catch (e) {
          patchJob({
            kind,
            title,
            status: isCancelled(e) ? "cancelled" : "error",
            report: null,
            error: isCancelled(e)
              ? // Deliberately blunt. This endpoint PERSISTS: unlike a Discover
                // run, where stopping simply means fewer calls, here the server
                // may already have re-predicted and written the row. Saying
                // "cancelled" flat would be a lie the reader could act on.
                "Stopped waiting. This one saves its result, so if the server had " +
                "already finished, the new prediction may still have been written — " +
                "the row below is refreshed to show whatever actually landed."
              : e instanceof Error ? e.message : "Re-prediction failed.",
            at: Date.now(),
          });
        } finally {
          delete repredictAbort.current[key];
        }
      })();
    },
    [announce],
  );

  /** Stop waiting on one saved-book re-prediction. See the note the job carries:
   *  the server write may still land, which is why the card refreshes its row on
   *  a cancel exactly as it does on a completion. */
  const cancelQueueRepredict = useCallback((kind: BookKind, title: string) => {
    repredictAbort.current[queueRepredictKey(kind, title)]?.abort();
  }, []);

  /** Drop a finished job's result — the card's dismiss, and what stops a stale
   *  report from reappearing when the reader expands that card again later. */
  const clearQueueRepredict = useCallback((kind: BookKind, title: string) => {
    const key = queueRepredictKey(kind, title);
    setQueueRepredicts((prev) => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      queueRepredictsRef.current = next;
      return next;
    });
  }, []);

  const patchGenreTab = useCallback(
    (p: Partial<GenreTabState>) => setGenreTab((s) => ({ ...s, ...p })),
    [],
  );
  const requestFocusOnArrival = useCallback(() => {
    arrivalFocus.current = true;
  }, []);
  const consumeArrivalFocus = useCallback(() => {
    const pending = arrivalFocus.current;
    arrivalFocus.current = false;
    return pending;
  }, []);

  const value = useMemo<PredictJobsValue>(
    () => ({
      runs,
      queueRepredicts,
      startQueueRepredict,
      clearQueueRepredict,
      cancelQueueRepredict,
      cancelRun,
      dismissCancelled,
      activeKind,
      setActiveKind,
      notice,
      dismissNotice,
      setRequest,
      genreTab,
      patchGenreTab,
      requestFocusOnArrival,
      consumeArrivalFocus,
      dismissInterrupted,
      dismissGenError,
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
      runs, queueRepredicts, startQueueRepredict, clearQueueRepredict,
      cancelQueueRepredict, cancelRun, dismissCancelled, activeKind, notice,
      dismissNotice, setRequest, genreTab, patchGenreTab, requestFocusOnArrival,
      consumeArrivalFocus, dismissInterrupted, dismissGenError, generate, score, refineOne,
      refineRemaining, repredictOne, save, removeBook, restoreAll,
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
