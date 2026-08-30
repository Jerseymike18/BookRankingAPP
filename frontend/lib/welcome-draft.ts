/* ── The welcome wizard's draft, kept across reloads and navigations ─────────
 *
 * The five-window wizard used to hold every answer in React state and write the
 * lot in one go from the final window. Anything that unmounted the page threw it
 * all away — and the most likely thing to unmount it is the wizard's own last
 * step: a reader who goes looking for the import page before finishing is bounced
 * back to /welcome by the proxy (they are not onboarded yet), landing on window 1
 * with the authors and genres they had just typed gone.
 *
 * Two things now keep that from happening. WelcomeClient commits each window to
 * the server as the reader leaves it, and this module mirrors the whole in-progress
 * draft so an interrupted wizard resumes exactly where it stopped — including the
 * windows that had not been committed yet.
 *
 * sessionStorage, not localStorage, for the same reason as the prediction runs
 * (`lib/predict-jobs`): the draft is one reader's answers, and a shared browser
 * must not hand them to whoever signs in next. Sign-out clears it outright.
 *
 * The codec below is pure and exported for `tests/welcome-draft.test.ts`: a bug
 * here is invisible until a reader reloads and finds their setup gone, or finds a
 * white page because a stale-shaped blob reached a `.map`.
 */

export const WELCOME_DRAFT_KEY = "trl:welcome-draft:v1";

/** Which windows have already been written to the server. Kept in the draft so a
 *  resumed wizard doesn't re-send work that is already saved, and — more
 *  importantly — still knows what it owes if it was interrupted mid-flow. */
export type WelcomeSaved = {
  prefs: boolean;
  /** Genres whose weight override is currently stored (so a later revert can
   *  clear it rather than leaving the server disagreeing with the screen). */
  weights: string[];
  anchors: boolean;
};

export type WelcomeDraft = {
  step: number;
  mode: "keep" | "customize";
  lengthPref: number;
  favAuthors: string[];
  favGenres: string[];
  anchorMode: "keep" | "customize";
  /** band key → typed value, as typed. */
  anchorRaw: Record<string, string>;
  /** genre → category → typed relative weight, as typed. */
  weightRaw: Record<string, Record<string, string>>;
  saved: WelcomeSaved;
};

export const EMPTY_SAVED: WelcomeSaved = { prefs: false, weights: [], anchors: false };

/* ── Codec ──────────────────────────────────────────────────────────────── */

const MAX_LIST = 20; // generous cap on the favourites arrays; keeps a blob bounded

const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((s): s is string => typeof s === "string").slice(0, MAX_LIST) : [];

/** A flat string→string map, with every non-string entry dropped. */
const strMap = (v: unknown): Record<string, string> => {
  if (!v || typeof v !== "object" || Array.isArray(v)) return {};
  const out: Record<string, string> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (typeof val === "string") out[k] = val;
  }
  return out;
};

const modeOf = (v: unknown): "keep" | "customize" =>
  v === "customize" ? "customize" : "keep";

export function toWelcomeDraft(d: WelcomeDraft): WelcomeDraft {
  return {
    step: d.step,
    mode: d.mode,
    lengthPref: d.lengthPref,
    favAuthors: d.favAuthors,
    favGenres: d.favGenres,
    anchorMode: d.anchorMode,
    anchorRaw: d.anchorRaw,
    weightRaw: d.weightRaw,
    saved: d.saved,
  };
}

/** Parse a stored draft, or null if it is unusable. Every field is shape-checked
 *  rather than trusted: the wizard renders straight off these, so a corrupt or
 *  stale-shaped blob would otherwise take the page down on load, recoverable only
 *  by clearing storage. `stepCount` bounds the restored window so a draft written
 *  by a longer wizard can't land on a window that no longer exists. */
export function fromWelcomeDraft(raw: string, stepCount: number): WelcomeDraft | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const s = parsed as Record<string, unknown>;

  const step =
    typeof s.step === "number" && Number.isFinite(s.step)
      ? Math.min(Math.max(0, Math.floor(s.step)), Math.max(0, stepCount - 1))
      : 0;

  const weightRaw: Record<string, Record<string, string>> = {};
  if (s.weightRaw && typeof s.weightRaw === "object" && !Array.isArray(s.weightRaw)) {
    for (const [genre, cats] of Object.entries(s.weightRaw as Record<string, unknown>)) {
      weightRaw[genre] = strMap(cats);
    }
  }

  const savedSrc =
    s.saved && typeof s.saved === "object" && !Array.isArray(s.saved)
      ? (s.saved as Record<string, unknown>)
      : {};

  return {
    step,
    mode: modeOf(s.mode),
    lengthPref:
      typeof s.lengthPref === "number" && Number.isFinite(s.lengthPref) ? s.lengthPref : 0,
    favAuthors: strList(s.favAuthors),
    favGenres: strList(s.favGenres),
    anchorMode: modeOf(s.anchorMode),
    anchorRaw: strMap(s.anchorRaw),
    weightRaw,
    saved: {
      prefs: savedSrc.prefs === true,
      weights: strList(savedSrc.weights),
      anchors: savedSrc.anchors === true,
    },
  };
}

/** Is there anything in this draft worth restoring? A wizard that has been opened
 *  and not touched should not write a snapshot, and a snapshot of nothing should
 *  not announce itself as resumed work. */
export function isEmptyDraft(d: WelcomeDraft): boolean {
  return (
    d.step === 0 &&
    d.mode === "keep" &&
    d.anchorMode === "keep" &&
    d.lengthPref === 0 &&
    !d.favAuthors.some((s) => s.trim()) &&
    !d.favGenres.some((s) => s.trim()) &&
    !d.saved.prefs &&
    d.saved.weights.length === 0 &&
    !d.saved.anchors
  );
}

/* ── Storage ────────────────────────────────────────────────────────────── */

// Latched once the draft is deliberately discarded, so nothing can re-create it.
// Both callers then navigate, and each awaits something first: sign-out awaits
// Supabase, finishing awaits the last write. A state change landing inside that
// window would fire the wizard's mirror effect and rewrite the draft we just
// deleted. The flag lives until the next full page load, which both perform.
let stopped = false;

/** Wipe the stored draft. Called when onboarding completes (everything is on the
 *  server by then, and a leftover draft would offer to resume a finished wizard)
 *  and on sign-out (so a shared browser never resumes someone else's setup). */
export function clearWelcomeDraft(): void {
  stopped = true;
  try {
    window.sessionStorage.removeItem(WELCOME_DRAFT_KEY);
  } catch {
    /* storage disabled — nothing to clear */
  }
}

export function readWelcomeDraft(stepCount: number): WelcomeDraft | null {
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(WELCOME_DRAFT_KEY);
  } catch {
    return null; // storage disabled — run purely in memory
  }
  return raw ? fromWelcomeDraft(raw, stepCount) : null;
}

export function writeWelcomeDraft(d: WelcomeDraft): void {
  if (stopped) return;
  try {
    window.sessionStorage.setItem(WELCOME_DRAFT_KEY, JSON.stringify(toWelcomeDraft(d)));
  } catch {
    /* over quota or disabled — persistence is a bonus, never a blocker */
  }
}
