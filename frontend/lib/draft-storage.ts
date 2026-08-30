/* ── Where in-progress work is parked so a navigation can't eat it ───────────
 *
 * Several pages hold work the reader has done but not yet submitted: the setup
 * wizard's answers, a book being rated across 14 components. Held only in React
 * state, all of it dies on any unmount — a nav click, a reload, a session that
 * expired and bounced them to /login. This module is the shared plumbing those
 * per-page drafts sit on; each page owns its own codec, since only the page knows
 * which of its fields are worth restoring and which must be re-derived.
 *
 * sessionStorage, not localStorage, and the same reasoning throughout: a draft is
 * one reader's unfinished work, scoped to the tab they are working in, and a
 * shared browser must never hand it to whoever signs in next.
 *
 * EVERY KEY IS DECLARED HERE, in `DRAFT_KEYS`, rather than registered by the
 * module that uses it. `clearAllDrafts()` runs from the sign-out handler in
 * components/Nav, which does not import the page modules — a self-registering
 * store would therefore clear only the drafts whose page the reader happened to
 * have visited, and silently leave the rest behind for the next person. Adding a
 * key here is what makes a new draft get wiped on sign-out.
 */

export const DRAFT_KEYS = {
  welcome: "trl:welcome-draft:v1",
  addBook: "trl:add-book-draft:v1",
} as const;

export type DraftStore = {
  /** The stored blob verbatim, or null if there is none or storage is off.
   *  Parsing is the caller's: only the page knows which of its fields are worth
   *  restoring and which must be re-derived from live server data. */
  read(): string | null;
  write(value: unknown): void;
  /** Forget the draft, but keep the slot live. For a page the reader stays on
   *  after finishing — /add-book resets its form and is immediately ready for the
   *  next book, so latching the slot off would silently stop protecting it. */
  discard(): void;
  /** Forget the draft AND stop it being re-created for the rest of this page's
   *  life. Only for a page that is navigating away: it exists because the caller
   *  awaits something before it goes, and a state change landing inside that
   *  window would fire the page's mirror effect and rewrite what we just deleted. */
  clear(): void;
};

// Set by clearAllDrafts. Both callers navigate immediately afterwards, and each
// awaits something first (sign-out awaits Supabase); a state change landing inside
// that window would fire a page's mirror effect and rewrite what we just deleted.
let allStopped = false;

const remove = (key: string) => {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* storage disabled — nothing to clear */
  }
};

/** One page's draft slot. */
export function draftStore(
  key: (typeof DRAFT_KEYS)[keyof typeof DRAFT_KEYS]
): DraftStore {
  // Per-store, so one page discarding its own draft (on a successful submit)
  // doesn't also silence another page's.
  let stopped = false;
  return {
    read() {
      try {
        return window.sessionStorage.getItem(key);
      } catch {
        return null; // storage disabled — run purely in memory
      }
    },
    write(value: unknown) {
      if (stopped || allStopped) return;
      try {
        window.sessionStorage.setItem(key, JSON.stringify(value));
      } catch {
        /* over quota or disabled — persistence is a bonus, never a blocker */
      }
    },
    discard() {
      remove(key);
    },
    clear() {
      stopped = true;
      remove(key);
    },
  };
}

/** Wipe every page's draft. Called on sign-out. */
export function clearAllDrafts(): void {
  allStopped = true;
  for (const key of Object.values(DRAFT_KEYS)) remove(key);
}
