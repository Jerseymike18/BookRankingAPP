/* ── The book being rated on /add-book, kept across reloads and navigations ──
 *
 * Adding a book is the heaviest data entry in the app: title, author, genre,
 * series and volume, word count, year and month, and then FOURTEEN component
 * scores typed one box at a time. All of it lived in React state and was written
 * only by Submit, so anything that unmounted the page threw the lot away — a nav
 * click, a reload, a mistaken back gesture, or an expired session bouncing the
 * reader to /login mid-entry (`proxy.ts` redirects the next navigation once the
 * token can't be refreshed).
 *
 * Unlike the welcome wizard there is no partial commit to make here: `add_book`
 * validates that every required component is present and refuses to store a
 * half-rated book (HARD CONSTRAINT 3's fixed schema is the reason — a books row
 * with missing components would feed the engine). So the answer is the draft
 * alone: mirror what's typed, restore it on return, and drop it the moment the
 * book is actually saved.
 *
 * The codec is pure and exported for `tests/add-book-draft.test.ts`, for the same
 * reason as the welcome one: it fails silently, and a reader only finds out when
 * the restore hands them someone's stale genre or a white page.
 */

import { DRAFT_KEYS, draftStore } from "./draft-storage";
import type { BookKind } from "./types";

export type AddBookDraft = {
  kind: BookKind;
  title: string;
  author: string;
  genre: string;
  series: string;
  seriesNumber: number | null;
  words: number;
  yearRead: number;
  monthRead: number;
  /** component name → the box's raw text (empty is meaningful: "not rated yet"). */
  scores: Record<string, string>;
  /** Whether the metadata came from a lookup — drives the "prefilled" hint. */
  prefilled: boolean;
};

/* ── Codec ──────────────────────────────────────────────────────────────── */

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) ? v : fallback;

/** Parse a stored draft, or null if it is unusable.
 *
 *  `validComponents` and `validGenres` are the CURRENT server truth, passed in by
 *  the page: a genre the reader has since deleted, or a component that is not part
 *  of the restored `kind`'s schema, is dropped rather than restored. Putting a
 *  dead genre back into the select would submit a book the backend rejects, and a
 *  stray component key would be sent as a score for a column that doesn't exist. */
export function fromAddBookDraft(
  raw: string,
  validComponents: (kind: BookKind) => string[],
  validGenres: string[]
): AddBookDraft | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const s = parsed as Record<string, unknown>;

  const kind: BookKind = s.kind === "nonfiction" ? "nonfiction" : "fiction";

  const scores: Record<string, string> = {};
  if (s.scores && typeof s.scores === "object" && !Array.isArray(s.scores)) {
    const allowed = new Set(validComponents(kind));
    for (const [comp, v] of Object.entries(s.scores as Record<string, unknown>)) {
      if (typeof v === "string" && allowed.has(comp)) scores[comp] = v;
    }
  }

  const genre = str(s.genre);
  const now = new Date();
  return {
    kind,
    title: str(s.title),
    author: str(s.author),
    // Nonfiction has no genre field; for fiction, only a genre the reader still has.
    genre: validGenres.includes(genre) ? genre : "",
    series: str(s.series),
    seriesNumber:
      typeof s.seriesNumber === "number" && Number.isFinite(s.seriesNumber)
        ? s.seriesNumber
        : null,
    words: Math.max(0, num(s.words, 0)),
    yearRead: num(s.yearRead, now.getFullYear()),
    monthRead: num(s.monthRead, now.getMonth() + 1),
    scores,
    prefilled: s.prefilled === true,
  };
}

/** Has the reader actually entered anything? An untouched form must not be stored
 *  (nothing to protect) and a stored blank must not announce itself as restored
 *  work. Year and month are excluded on purpose — they default to today, so they
 *  are not evidence that anyone typed anything. */
export function isEmptyAddBookDraft(d: AddBookDraft): boolean {
  return (
    !d.title.trim() &&
    !d.author.trim() &&
    !d.series.trim() &&
    d.seriesNumber === null &&
    d.words === 0 &&
    !Object.values(d.scores).some((v) => v.trim())
  );
}

/** How much of the form is filled in, for the "restored" line. Counting only the
 *  component boxes: it is the part that represents real work. */
export function scoredCount(d: AddBookDraft): number {
  return Object.values(d.scores).filter((v) => v.trim()).length;
}

/* ── Storage ────────────────────────────────────────────────────────────── */

const store = draftStore(DRAFT_KEYS.addBook);

/** Drop the draft — on a successful save, and whenever the form goes empty.
 *  `discard`, NOT `clear`: the reader stays on this page and adds the next book,
 *  so latching the slot off here would leave every book after the first one
 *  unprotected. */
export const clearAddBookDraft = () => store.discard();

export function readAddBookDraft(
  validComponents: (kind: BookKind) => string[],
  validGenres: string[]
): AddBookDraft | null {
  const raw = store.read();
  return raw ? fromAddBookDraft(raw, validComponents, validGenres) : null;
}

export const writeAddBookDraft = (d: AddBookDraft): void => store.write(d);
