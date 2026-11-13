"use client";

import React, { useState, useMemo, useCallback, useEffect, useTransition } from "react";
import { useRouter } from "next/navigation";
import { editRating, deleteBook, fetchValidGenres, updateBookMetadata } from "@/lib/api";
import type { BookMetadataPayload } from "@/lib/api";
import type { BooksResponse, Book, CategoryComponents, BookKind, CombinedRankRow } from "@/lib/types";
import { seriesLabel, componentLabel } from "@/lib/format";
import { useReadOnly } from "@/lib/readonly-context";
import { useSortable, MobileSortBar } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";
import { TypeToggle, type TypeScope } from "@/components/TypeToggle";
import { ProgressBar } from "@/components/ProgressBar";
import { SegmentedTabs } from "@/components/SegmentedTabs";
import { useRowLimit, ShowMoreBar } from "@/components/ShowMore";

/* ── Helpers ──────────────────────────────────────────────────────────── */

function spineClass(wa: number): string {
  if (wa >= 9.5) return "spine-sp";
  if (wa >= 8.5) return "spine-s";
  if (wa >= 7.5) return "spine-a";
  if (wa >= 6.5) return "spine-b";
  if (wa >= 5.5) return "spine-c";
  if (wa >= 4.5) return "spine-d";
  return "spine-f";
}

/** A missing/null score renders as an empty box, not 0, so the edit form can
 * distinguish "no value yet" from a real 0. */
function flattenComponentsToStrings(components: CategoryComponents): Record<string, string> {
  const flat: Record<string, string> = {};
  for (const comps of Object.values(components)) {
    for (const [comp, val] of Object.entries(comps)) {
      flat[comp] = val != null ? String(val) : "";
    }
  }
  return flat;
}

/* ── Read-only component grid ─────────────────────────────────────────── */

function ComponentGrid({
  components,
  categoryOrder,
  kind,
}: {
  components: CategoryComponents;
  categoryOrder: string[];
  kind: BookKind;
}) {
  return (
    <div className="mt-4 space-y-3">
      {categoryOrder.map((cat) => {
        const comps = components[cat];
        if (!comps || Object.keys(comps).length === 0) return null;
        return (
          <div key={cat}>
            <p
              className="text-xs font-semibold uppercase tracking-widest mb-2"
              style={{ color: "var(--color-muted)" }}
            >
              {cat}
            </p>
            <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(5rem, 1fr))" }}>
              {Object.entries(comps).map(([comp, val]) => (
                <div key={comp} className="comp-tile">
                  <span className="comp-label">{componentLabel(comp, kind)}</span>
                  <span className="comp-value">
                    {val !== null ? val.toFixed(1) : "—"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Editable score grid ─────────────────────────────────────────────── */

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface-2)",
  border: "1px solid transparent",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

/* Raw string state so a box can go empty without snapping back to 0; empty
 * means "leave this component's stored value unchanged" (see handleSave). */
const SCORE_INPUT_RE = /^-?\d*\.?\d*$/;

function clampScoreInput(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === "") return raw;
  const v = parseFloat(trimmed);
  if (isNaN(v)) return raw;
  const clamped = Math.min(10, Math.max(0, v));
  return clamped === v ? raw : String(clamped);
}

/** Only fields with a real, parseable value are included — empty/unparseable
 * boxes are omitted so change_rating()/change_nonfiction_rating() leave them
 * unchanged rather than overwriting with 0. */
function buildChangedScores(raw: Record<string, string>): Record<string, number> {
  const payload: Record<string, number> = {};
  for (const [comp, str] of Object.entries(raw)) {
    const trimmed = str.trim();
    if (trimmed === "") continue;
    const v = parseFloat(trimmed);
    if (isNaN(v)) continue;
    payload[comp] = Math.min(10, Math.max(0, v));
  }
  return payload;
}

function ScoreGrid({
  components,
  categoryOrder,
  scores,
  onChange,
  kind,
}: {
  components: CategoryComponents;
  categoryOrder: string[];
  scores: Record<string, string>;
  onChange: (comp: string, val: string) => void;
  kind: BookKind;
}) {
  return (
    <div className="space-y-5">
      {categoryOrder.map((cat) => {
        const comps = components[cat];
        if (!comps) return null;
        return (
          <div key={cat}>
            <p
              className="text-xs font-semibold uppercase tracking-widest mb-2"
              style={{ color: "var(--color-muted)" }}
            >
              {cat}
            </p>
            <div
              className="grid gap-3"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))" }}
            >
              {Object.keys(comps).map((comp) => (
                <div key={comp}>
                  <label
                    className="block text-xs mb-1"
                    style={{ color: "var(--color-muted)" }}
                  >
                    {componentLabel(comp, kind)}
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={10}
                    step={0.1}
                    value={scores[comp] ?? ""}
                    onChange={(e) => {
                      const raw = e.target.value;
                      if (raw === "" || SCORE_INPUT_RE.test(raw)) onChange(comp, raw);
                    }}
                    onBlur={(e) => onChange(comp, clampScoreInput(e.target.value))}
                    className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
                    style={inputStyle}
                  />
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Editable metadata form ──────────────────────────────────────────────
   Mirrors the score grid's raw-string / omit-unchanged discipline: numeric
   boxes (words, year read, series #) hold raw strings so they can go empty,
   and a blank field means "leave the stored value as-is" (see buildMetaPayload).
   Genre is a dropdown constrained to the valid genres so a typo can't be sent;
   title is editable but flagged as a rename. ─────────────────────────────── */

type MetaForm = {
  title: string;
  author: string;
  genre: string;
  series: string;
  series_number: string;
  words: string;
  year_read: string;
};

function metaFormFromBook(book: Book): MetaForm {
  return {
    title: book.title,
    author: book.author ?? "",
    genre: book.genre ?? "",
    series: book.series ?? "",
    series_number: book.series_number != null ? String(book.series_number) : "",
    words: book.words != null ? String(book.words) : "",
    year_read: book.year_read != null ? String(book.year_read) : "",
  };
}

const NUM_INPUT_RE = /^\d*\.?\d*$/;

/** Build a partial metadata payload holding only fields the user actually
 * changed. Blank/empty inputs are omitted (leave-as-is); numeric fields are
 * parsed and only sent when they differ from the stored value. `title` is
 * included only on a real rename. */
function buildMetaPayload(book: Book, form: MetaForm): BookMetadataPayload {
  const payload: BookMetadataPayload = {};

  const t = form.title.trim();
  if (t && t !== book.title) payload.title = t;

  const a = form.author.trim();
  if (a && a !== (book.author ?? "")) payload.author = a;

  if (form.genre && form.genre !== book.genre) payload.genre = form.genre;

  const s = form.series.trim();
  if (s && s !== (book.series ?? "")) payload.series = s;

  const sn = form.series_number.trim();
  if (sn !== "") {
    const v = parseFloat(sn);
    if (!isNaN(v) && v !== book.series_number) payload.series_number = v;
  }

  const w = form.words.trim();
  if (w !== "") {
    const v = parseInt(w, 10);
    if (!isNaN(v) && v !== book.words) payload.words = v;
  }

  const y = form.year_read.trim();
  if (y !== "") {
    const v = parseInt(y, 10);
    if (!isNaN(v) && v !== book.year_read) payload.year_read = v;
  }

  return payload;
}

function MetaField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function MetadataForm({
  form,
  validGenres,
  onChange,
  renameWarning,
}: {
  form: MetaForm;
  validGenres: string[];
  onChange: (field: keyof MetaForm, val: string) => void;
  renameWarning: boolean;
}) {
  // Always include the current genre in the option list, even if it isn't in
  // the valid set yet (e.g. a legacy value), so the select renders correctly.
  const genreOptions = useMemo(() => {
    const set = new Set(validGenres);
    if (form.genre) set.add(form.genre);
    return Array.from(set).sort();
  }, [validGenres, form.genre]);

  const num = (field: keyof MetaForm) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    if (raw === "" || NUM_INPUT_RE.test(raw)) onChange(field, raw);
  };

  return (
    <div
      className="grid gap-3"
      style={{ gridTemplateColumns: "repeat(auto-fill, minmax(11rem, 1fr))" }}
    >
      <MetaField label="Title (rename)">
        <input
          type="text"
          value={form.title}
          onChange={(e) => onChange("title", e.target.value)}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        />
        {renameWarning && (
          <p className="text-xs mt-1" style={{ color: "var(--color-spine-c)" }}>
            Renames the book everywhere (queue, TBR &amp; delta log follow).
          </p>
        )}
      </MetaField>

      <MetaField label="Author">
        <input
          type="text"
          value={form.author}
          onChange={(e) => onChange("author", e.target.value)}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        />
      </MetaField>

      <MetaField label="Genre">
        <select
          value={form.genre}
          onChange={(e) => onChange("genre", e.target.value)}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        >
          {!form.genre && <option value="">— choose —</option>}
          {genreOptions.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
      </MetaField>

      <MetaField label="Series">
        <input
          type="text"
          value={form.series}
          onChange={(e) => onChange("series", e.target.value)}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        />
      </MetaField>

      <MetaField label="Series #">
        <input
          type="text"
          inputMode="decimal"
          value={form.series_number}
          onChange={num("series_number")}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        />
      </MetaField>

      <MetaField label="Words">
        <input
          type="text"
          inputMode="numeric"
          value={form.words}
          onChange={num("words")}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        />
      </MetaField>

      <MetaField label="Year read">
        <input
          type="text"
          inputMode="numeric"
          value={form.year_read}
          onChange={num("year_read")}
          className="w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        />
      </MetaField>
    </div>
  );
}

/* ── Expanded book panel ─────────────────────────────────────────────── */

type CardMode = "view" | "edit" | "edit-meta" | "confirm-delete";

function BookExpandedPanel({
  book,
  categoryOrder,
  kind,
  onRefresh,
  refreshing,
  onClose,
}: {
  book: Book;
  categoryOrder: string[];
  kind: BookKind;
  onRefresh: () => void;
  /** True while the post-save refetch is still in flight (see onRefresh). */
  refreshing: boolean;
  onClose: () => void;
}) {
  const ro = useReadOnly();
  const [mode, setMode] = useState<CardMode>("view");
  const [scores, setScores] = useState<Record<string, string>>({});
  const [metaForm, setMetaForm] = useState<MetaForm>(() => metaFormFromBook(book));
  const [validGenres, setValidGenres] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Load the valid-genre list once we enter metadata editing, so the genre
  // dropdown is constrained to real genres (a typo can't be submitted).
  useEffect(() => {
    if (mode !== "edit-meta" || validGenres.length > 0) return;
    let cancelled = false;
    fetchValidGenres(kind)
      .then((g) => { if (!cancelled) setValidGenres(g); })
      .catch(() => { /* dropdown still shows the current genre; non-fatal */ });
    return () => { cancelled = true; };
  }, [mode, validGenres.length, kind]);

  function enterEdit() {
    setScores(flattenComponentsToStrings(book.components));
    setMode("edit");
    setActionError(null);
    setSaveSuccess(false);
  }

  function enterEditMeta() {
    setMetaForm(metaFormFromBook(book));
    setMode("edit-meta");
    setActionError(null);
    setSaveSuccess(false);
  }

  function cancelEdit() {
    setMode("view");
    setActionError(null);
  }

  async function handleSaveMeta() {
    const payload = buildMetaPayload(book, metaForm);
    if (Object.keys(payload).length === 0) {
      setActionError("No metadata changes to save.");
      return;
    }
    setSaving(true);
    setActionError(null);
    try {
      await updateBookMetadata(book.title, payload, kind);
      setSaveSuccess(true);
      setMode("view");
      onRefresh();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Could not save metadata.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSave() {
    // Empty boxes mean "leave unchanged" — only send fields with a real value.
    const payload = buildChangedScores(scores);
    if (Object.keys(payload).length === 0) {
      setActionError("No changes to save — enter a value in at least one field.");
      return;
    }
    setSaving(true);
    setActionError(null);
    try {
      await editRating(book.title, payload, kind);
      setSaveSuccess(true);
      setMode("view");
      onRefresh();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setActionError(null);
    try {
      await deleteBook(book.title, kind);
      onRefresh();
      onClose();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Could not delete book.");
      setDeleting(false);
      setMode("view");
    }
  }

  return (
    <div
      className="px-5 py-4"
      style={{ borderTop: "1px solid var(--color-rule)", background: "var(--color-surface-2)" }}
    >
      {/* ── Confirm-delete panel ── */}
      {mode === "confirm-delete" && (
        <div
          className="rounded-xl p-4 mb-4"
          style={{ background: "var(--color-danger-bg)", border: "1px solid var(--color-danger-border)" }}
        >
          <p className="text-sm font-semibold mb-1" style={{ color: "var(--color-danger)" }}>
            Delete &ldquo;{book.title}&rdquo;?
          </p>
          <p className="text-sm mb-4" style={{ color: "var(--color-danger)" }}>
            This permanently removes it from your library and all stats and rankings.
          </p>
          <div className="flex gap-3">
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-danger)", color: "var(--color-on-accent)" }}
            >
              {deleting ? "Deleting…" : "Yes, delete"}
            </button>
            <button
              onClick={() => { setMode("view"); setActionError(null); }}
              disabled={deleting}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{
                background: "var(--color-surface-2)",
                color: "var(--color-muted)",
                border: "1px solid var(--color-rule)",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Error banner ── */}
      {actionError && (
        <div
          className="rounded-lg px-4 py-3 text-sm mb-4"
          style={{ background: "var(--color-danger-bg)", color: "var(--color-danger)", border: "1px solid var(--color-danger-border)" }}
        >
          {actionError}
        </div>
      )}

      {/* ── Save success banner ── */}
      {saveSuccess && mode === "view" && (
        <div
          className="rounded-lg px-4 py-3 text-sm mb-4"
          style={{
            background: "var(--color-sage-light)",
            color: "var(--color-sage)",
            border: "1px solid var(--color-sage)",
          }}
        >
          Saved. Rankings are refreshing…
        </div>
      )}

      {/* ── View mode: read-only scores + action buttons ── */}
      {mode === "view" && (
        <>
          <ComponentGrid components={book.components} categoryOrder={categoryOrder} kind={kind} />
          {!ro && (
          <div className="flex gap-3 mt-5">
            <button
              onClick={enterEdit}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{ background: "var(--color-sage)", color: "var(--color-on-accent)" }}
            >
              Edit scores
            </button>
            <button
              onClick={enterEditMeta}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{
                background: "transparent",
                color: "var(--color-sage)",
                border: "1px solid var(--color-sage)",
              }}
            >
              Edit metadata
            </button>
            <button
              onClick={() => { setMode("confirm-delete"); setActionError(null); }}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{
                background: "transparent",
                color: "var(--color-danger)",
                border: "1px solid var(--color-danger-border)",
              }}
            >
              Delete book
            </button>
          </div>
          )}
        </>
      )}

      {/* ── Edit mode: score inputs + save/cancel ── */}
      {mode === "edit" && (
        <>
          <ScoreGrid
            components={book.components}
            categoryOrder={categoryOrder}
            scores={scores}
            kind={kind}
            onChange={(comp, val) =>
              setScores((prev) => ({ ...prev, [comp]: val }))
            }
          />
          <div className="flex gap-3 mt-5">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-sage)", color: "var(--color-on-accent)" }}
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button
              onClick={cancelEdit}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{
                background: "transparent",
                color: "var(--color-muted)",
                border: "1px solid var(--color-rule)",
              }}
            >
              Cancel
            </button>
          </div>
          {(saving || refreshing) && (
            // Saving a rating rewrites the row, then router.refresh() refetches
            // the whole ranking — the table below is stale until BOTH land, which
            // is why this tracks `refreshing` too. Without it the bar disappeared
            // when the POST returned, seconds before the numbers actually moved.
            <ProgressBar
              className="mt-3"
              label={saving ? "Saving your scores…" : "Re-ranking your library…"}
            />
          )}
        </>
      )}

      {/* ── Edit-metadata mode: author/genre/series/words/year/title ── */}
      {mode === "edit-meta" && (
        <>
          <p
            className="text-xs font-semibold uppercase tracking-widest mb-3"
            style={{ color: "var(--color-muted)" }}
          >
            Metadata — blank fields are left unchanged
          </p>
          <MetadataForm
            form={metaForm}
            validGenres={validGenres}
            renameWarning={metaForm.title.trim() !== book.title}
            onChange={(field, val) =>
              setMetaForm((prev) => ({ ...prev, [field]: val }))
            }
          />
          <div className="flex gap-3 mt-5">
            <button
              onClick={handleSaveMeta}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-sage)", color: "var(--color-on-accent)" }}
            >
              {saving ? "Saving…" : "Save metadata"}
            </button>
            <button
              onClick={cancelEdit}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{
                background: "transparent",
                color: "var(--color-muted)",
                border: "1px solid var(--color-rule)",
              }}
            >
              Cancel
            </button>
          </div>
          {saving && (
            <ProgressBar className="mt-3" label="Saving metadata and re-ranking…" />
          )}
        </>
      )}

      {deleting && (
        <ProgressBar className="mt-3" label="Removing the book and re-ranking…" />
      )}
    </div>
  );
}

/* ── Column definitions ───────────────────────────────────────────────── */

// Short column headers per category (fiction + nonfiction 2026 redesign).
const CAT_ABBREV: Record<string, string> = {
  Story: "Story", Character: "Char", Aesthetics: "Aes", Theme: "Theme",
  Worldbuilding: "WB",
  Substance: "Subst", Reasoning: "Reason", Exposition: "Expos", Impact: "Impact",
};

/* ── Individual component columns ──────────────────────────────────────────
   Every category header can expand into its own component sub-columns, each
   independently sortable. Purely presentational: the 14-component / 5-category
   rating structure is untouched, and the values come straight off the payload
   the expand-a-row panel already renders (book.components) — nothing is
   recomputed here. */

// Keyed by the *display* label (post componentLabel), so the nonfiction
// renames ("Enjoyment", "Insight") get abbreviated too. Anything unlisted
// renders in full; the header carries the full name as a tooltip either way.
const COMP_ABBREV: Record<string, string> = {
  Entertainment: "Enter", Enjoyment: "Enjoy", "Emotional Impact": "Emotion",
  Motivations: "Motive", "Thought-Provokingness": "Thought", Depth2: "Depth",
  Originality: "Orig", Integration: "Integr", Narration: "Narr",
  Informativeness: "Info", Argumentation: "Argument",
};

/** Namespaced so a component can never collide with a category or meta column
 *  (fiction "Depth" lives in Character, "Depth2" in Worldbuilding). */
function compColKey(cat: string, comp: string): string {
  return `comp:${cat}:${comp}`;
}

function compHeader(comp: string, kind: BookKind): string {
  const label = componentLabel(comp, kind);
  return COMP_ABBREV[label] ?? label;
}

// The primary ranking score is WA (the weighted score) for BOTH tracks — nonfiction
// now ranks by WA too (2026 redesign). Total Average is secondary and still drives
// tier bands + series rollups.
function primaryScore(b: Book): number {
  return b.wa ?? 0;
}

// Columns are built from the response's category_order (5 for fiction, 5 for
// nonfiction) so the same table serves both types; both lead with WA. An
// expanded category is followed by one sortable column per component it owns.
function buildCols(
  kind: BookKind,
  categoryOrder: string[],
  componentsByCat: Record<string, string[]>,
  expanded: Set<string>,
): ColDef<Book>[] {
  return [
    { key: "title", label: "Book", type: "string", getValue: (b) => b.title, align: "left" },
    {
      key: "wa", label: "WA",
      type: "numeric", getValue: (b) => primaryScore(b), align: "right",
      formatter: (v) => (v != null ? Number(v).toFixed(2) : "—"),
    },
    ...categoryOrder.flatMap((cat): ColDef<Book>[] => {
      const catCol: ColDef<Book> = {
        key: cat, label: CAT_ABBREV[cat] ?? cat, type: "numeric",
        getValue: (b) => (b.category_avgs ?? {})[cat] ?? 0, align: "right",
      };
      if (!expanded.has(cat)) return [catCol];
      return [
        catCol,
        ...(componentsByCat[cat] ?? []).map((comp): ColDef<Book> => ({
          key: compColKey(cat, comp), label: compHeader(comp, kind), type: "numeric",
          getValue: (b) => b.components?.[cat]?.[comp] ?? 0, align: "right",
        })),
      ];
    }),
    { key: "genre", label: "Genre", type: "string", getValue: (b) => b.genre, align: "left" },
  ];
}

/* ── Sub-tab bar ──────────────────────────────────────────────────────── */

type YearTab = string; // "all" or a year read, e.g. "2023"
type YearTabDef = { id: YearTab; label: string };

function SubTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: YearTabDef[];
  active: YearTab;
  onChange: (t: YearTab) => void;
}) {
  return <SegmentedTabs tabs={tabs} value={active} onChange={onChange} />;
}

/* ── Book list ────────────────────────────────────────────────────────────
   THE rankings format at every screen size (owner's call, 2026-11-05: the
   list beat the wide table). Shared by the per-type and the All rankings: one
   tappable row per book
   (rank, WA badge, title + author, the active sort value when it isn't WA),
   expanding into the same BookExpandedPanel the desktop table uses. */

type BookListItem = { book: Book; kind: BookKind; categoryOrder: string[]; key: string };

function BookMobileList<T>({
  items,
  cols,
  sortState,
  onSort,
  primaryKey,
  expandedKey,
  setExpandedKey,
  onRefresh,
  refreshing,
  sortRows,
  typeLabel = false,
}: {
  items: BookListItem[];
  cols: ColDef<T>[];
  sortState: { key: string; dir: "asc" | "desc" };
  onSort: (key: string) => void;
  primaryKey: string;
  expandedKey: string | null;
  setExpandedKey: (k: string | null) => void;
  onRefresh: () => void;
  refreshing: boolean;
  /** The rows `cols` read from, index-aligned with `items` (defaults to the books). */
  sortRows?: T[];
  /** Show Fiction / Nonfiction in the subtitle (the All view). */
  typeLabel?: boolean;
}) {
  const sortCol = cols.find((c) => c.key === sortState.key);
  const showSortVal = !!sortCol && sortCol.type === "numeric" && sortCol.key !== primaryKey;
  // Open short; the reader expands into the full library on request.
  const { visible, ...limit } = useRowLimit(items);
  return (
    <div>
      <MobileSortBar cols={cols} sortState={sortState} onSort={onSort} />
      {items.length === 0 ? (
        <p className="text-center py-16 text-sm" style={{ color: "var(--color-muted)" }}>
          No books match your filters.
        </p>
      ) : (
        <ul
          className="rounded-xl overflow-hidden"
          style={{ background: "var(--color-surface)", boxShadow: "var(--shadow-card)" }}
        >
          {visible.map(({ book, kind, categoryOrder, key }, i) => {
            const isExpanded = expandedKey === key;
            const row = (sortRows ? sortRows[i] : book) as T;
            const sortVal = showSortVal ? Number(sortCol!.getValue(row) ?? 0) : 0;
            return (
              <li
                key={key}
                className={spineClass(primaryScore(book))}
                style={{
                  borderLeftWidth: 3,
                  borderLeftStyle: "solid",
                  borderTop: i === 0 ? "none" : "1px solid var(--color-rule)",
                }}
              >
                <button
                  type="button"
                  onClick={() => setExpandedKey(isExpanded ? null : key)}
                  aria-expanded={isExpanded}
                  className="app-row w-full flex items-center gap-2 sm:gap-3 pl-2 pr-3 py-2.5 text-left"
                >
                  <span
                    className="w-5 shrink-0 text-right font-display italic text-sm"
                    style={{ color: "var(--color-faint)" }}
                  >
                    {i + 1}
                  </span>
                  <span className="wa-badge w-9! h-9! sm:w-11! sm:h-11!">{primaryScore(book).toFixed(2)}</span>
                  <span className="min-w-0 flex-1">
                    <span
                      className="font-display font-semibold text-sm leading-snug line-clamp-2 break-words"
                      style={{ color: "var(--color-ink)" }}
                    >
                      {book.title}
                    </span>
                    <span className="block text-xs mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
                      {book.author}
                      {typeLabel ? ` · ${kind === "nonfiction" ? "Nonfiction" : "Fiction"}` : ""}
                      {book.series ? ` · ${seriesLabel(book.series, book.series_number)}` : ` · ${book.genre}`}
                    </span>
                  </span>
                  {/* The wrapper does the hiding: .genre-chip is unlayered CSS and its own
                    display would beat a `hidden` utility on the same element. */}
                <span className="hidden sm:inline-flex shrink-0"><span className="genre-chip">{book.genre}</span></span>
                  {showSortVal && (
                    <span className="shrink-0 text-right">
                      <span className="block text-sm font-semibold tabular-nums" style={{ color: "var(--color-sage)" }}>
                        {sortVal ? sortVal.toFixed(sortCol!.key.startsWith("comp:") ? 1 : 2) : "—"}
                      </span>
                      <span className="block text-[11px]" style={{ color: "var(--color-faint)" }}>
                        {sortCol!.label}
                      </span>
                    </span>
                  )}
                  <svg
                    width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"
                    className="shrink-0 hidden sm:block"
                    style={{
                      color: "var(--color-faint)",
                      transform: isExpanded ? "rotate(90deg)" : "none",
                      transition: "transform 150ms",
                    }}
                  >
                    <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {isExpanded && (
                  <div style={{ borderTop: "1px solid var(--color-rule)" }}>
                    <BookExpandedPanel
                      book={book}
                      categoryOrder={categoryOrder}
                      kind={kind}
                      onRefresh={onRefresh}
                      refreshing={refreshing}
                      onClose={() => setExpandedKey(null)}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <ShowMoreBar {...limit} />
    </div>
  );
}

/* ── Per-type rankings (full table, one track) ────────────────────────────
   The original single-track rankings view: genre + search filters, year tabs
   (fiction), and the sortable table with the expand-to-edit panel. Unchanged
   apart from dropping its own <h1> — the wrapper below now owns the page title
   and the type toggle. */

function PerTypeRankings({
  data,
  kind = "fiction",
}: {
  data: BooksResponse;
  kind?: BookKind;
}) {
  const { books, genres, category_order } = data;
  const router = useRouter();
  const primaryKey = "wa";  // both tracks rank by WA (nonfiction 2026 redesign)

  const [yearTab, setYearTab] = useState<YearTab>("all");
  const [genreFilter, setGenreFilter] = useState<string>("All genres");
  const [search, setSearch] = useState("");
  const [expandedTitle, setExpandedTitle] = useState<string | null>(null);
  // No table columns to expand any more — the sort picker simply offers every
  // component of every category.
  const expandedCats = useMemo(() => new Set(category_order), [category_order]);

  // Component names per category, in schema order, unioned across the library
  // so a category still expands when the first book leaves one blank.
  const componentsByCat = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const cat of category_order) {
      const order: string[] = [];
      const seen = new Set<string>();
      for (const b of books) {
        for (const comp of Object.keys(b.components?.[cat] ?? {})) {
          if (!seen.has(comp)) { seen.add(comp); order.push(comp); }
        }
      }
      if (order.length > 0) map[cat] = order;
    }
    return map;
  }, [books, category_order]);

  const cols = useMemo(
    () => buildCols(kind, category_order, componentsByCat, expandedCats),
    [kind, category_order, componentsByCat, expandedCats],
  );

  // router.refresh() is FIRE-AND-FORGET: it returns immediately and the refetch
  // lands later. Running it inside a transition is what makes it observable —
  // `refreshing` stays true until the new server payload has rendered, which does
  // two things the previous bare call did not:
  //   * the "Saving and re-ranking your library…" bar can cover the actual
  //     re-rank instead of vanishing the moment the POST returns, leaving the
  //     reader watching a stale table with no indication anything is happening;
  //   * React keeps the CURRENT table on screen while the new one loads, instead
  //     of tearing down to the route's loading.tsx skeleton and back.
  const [refreshing, startRefresh] = useTransition();
  const onRefresh = useCallback(
    () => startRefresh(() => router.refresh()),
    [router],
  );

  // Year filter tabs derived from the data — any year the reader has, not a
  // hardcoded set. Offered only when there's more than one year to split by.
  const yearTabs = useMemo<YearTabDef[]>(() => {
    const years = [
      ...new Set(books.map((b) => b.year_read).filter((y): y is number => y != null)),
    ].sort((a, b) => b - a);
    return years.length > 1
      ? [{ id: "all", label: "All" }, ...years.map((y) => ({ id: String(y), label: String(y) }))]
      : [];
  }, [books]);

  const scopedBooks = useMemo(() => {
    if (yearTab === "all") return books;
    const yr = parseInt(yearTab, 10);
    return books.filter((b) => b.year_read === yr);
  }, [books, yearTab]);

  const filtered = useMemo(() => {
    let list = scopedBooks;
    if (genreFilter !== "All genres") {
      list = list.filter((b) => b.genre === genreFilter);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (b) =>
          b.title.toLowerCase().includes(q) ||
          b.author.toLowerCase().includes(q)
      );
    }
    return list;
  }, [scopedBooks, genreFilter, search]);

  const { sorted, sortState, handleSort } = useSortable(filtered, cols, { key: primaryKey, dir: "desc" });

  const handleGenre = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    setGenreFilter(e.target.value);
  }, []);

  const handleSearch = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
  }, []);

  const handleYearTab = useCallback((t: YearTab) => {
    setYearTab(t);
    setGenreFilter("All genres");
    setSearch("");
  }, []);


  return (
    <div>
      {/* Filters summary — the page title + type toggle live in the wrapper. */}

      {/* Year sub-tabs (fiction only — nonfiction books have no year tabs) */}
      {kind === "fiction" && yearTabs.length > 0 && (
        <SubTabs tabs={yearTabs} active={yearTab} onChange={handleYearTab} />
      )}

      {/* Controls */}
      <div className="flex flex-wrap gap-3 mb-6">
        <select
          value={genreFilter}
          onChange={handleGenre}
          className="px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={{
            background: "var(--color-surface-2)",
            border: "1px solid transparent",
            color: "var(--color-ink)",
            fontFamily: "var(--font-body)",
          }}
        >
          <option value="All genres">All genres</option>
          {genres.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>

        <div className="relative flex-1 min-w-52">
          <input
            type="text"
            placeholder="Search by title or author…"
            value={search}
            onChange={handleSearch}
            className="w-full pl-9 pr-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
            style={{
              background: "var(--color-surface-2)",
              border: "1px solid transparent",
              color: "var(--color-ink)",
              fontFamily: "var(--font-body)",
            }}
          />
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5"
            style={{ color: "var(--color-faint)" }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>

        <div
          className="flex items-center px-3 rounded-lg text-sm font-medium"
          style={{
            background: "var(--color-sage-light)",
            color: "var(--color-sage)",
          }}
        >
          {filtered.length}{" "}
          {genreFilter !== "All genres" ? `in ${genreFilter}` : "books"}
        </div>
      </div>

      {/* Phones: one tappable row per book; tap opens the same panel the
          desktop table expands into. */}
      <BookMobileList
        items={sorted.map((book) => ({ book, kind, categoryOrder: category_order, key: book.title }))}
        cols={cols}
        sortState={sortState}
        onSort={handleSort}
        primaryKey={primaryKey}
        expandedKey={expandedTitle}
        setExpandedKey={setExpandedTitle}
        onRefresh={onRefresh}
        refreshing={refreshing}
      />

    </div>
  );
}

/* ── Combined (All) ranking — cross-type, by WA ───────────────────────────
   Fiction and nonfiction WA use different weights, but both land on the same
   0–10 scale, so WA is a meaningful cross-type ordering — and it matches how
   each single-track table sorts. Total Average is kept as a secondary column.
   This reads the backend-computed `combined_ranking` (the same payload the
   Stats page uses); no derived math is reimplemented here. Columns are reduced
   because the two tracks' per-category sets differ. */


type CombinedItem = {
  book: Book;
  kind: BookKind;
  categoryOrder: string[];
  key: string;
  total_average: number | null;
};

const COMBINED_COLS: ColDef<CombinedItem>[] = [
  { key: "title", label: "Book", type: "string", getValue: (r) => r.book.title, align: "left" },
  { key: "wa", label: "WA", type: "numeric", getValue: (r) => primaryScore(r.book), align: "right", formatter: (v) => (v != null ? Number(v).toFixed(2) : "—") },
  { key: "total_average", label: "Total Avg", type: "numeric", getValue: (r) => r.total_average ?? 0, align: "right" },
  { key: "type", label: "Type", type: "string", getValue: (r) => r.kind, align: "left" },
  { key: "genre", label: "Genre", type: "string", getValue: (r) => r.book.genre, align: "left" },
];

/** The All view: both tracks in one WA leaderboard, drawn exactly like the
 *  per-type table — spine, title + author, the same phone list, and the same
 *  expand-to-edit panel (each row keeps its own track's categories). */
function CombinedRankings({
  rows,
  fiction,
  nonfiction,
}: {
  rows: CombinedRankRow[];
  fiction: BooksResponse;
  nonfiction: BooksResponse;
}) {
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const onRefresh = useCallback(() => startRefresh(() => router.refresh()), [router]);
  const [genreFilter, setGenreFilter] = useState<string>("All genres");
  const [search, setSearch] = useState("");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // Join the leaderboard rows back to the full Book records (components,
  // series, words) so the All view can show and edit exactly what the
  // per-type tables do. Total Average comes from the leaderboard row.
  const items = useMemo<CombinedItem[]>(() => {
    const byKey = new Map<string, CombinedItem>();
    for (const [kind, data] of [["fiction", fiction], ["nonfiction", nonfiction]] as const) {
      for (const book of data.books) {
        const key = `${kind}:${book.title}`;
        byKey.set(key, { book, kind, categoryOrder: data.category_order, key, total_average: book.total_average ?? null });
      }
    }
    for (const r of rows) {
      const it = byKey.get(`${r.type}:${r.title}`);
      if (it) it.total_average = r.total_average;
    }
    return [...byKey.values()];
  }, [rows, fiction, nonfiction]);

  const genres = useMemo(() => [...new Set(items.map((i) => i.book.genre))].sort(), [items]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter(
      (i) =>
        (genreFilter === "All genres" || i.book.genre === genreFilter) &&
        (!q || i.book.title.toLowerCase().includes(q) || i.book.author.toLowerCase().includes(q)),
    );
  }, [items, genreFilter, search]);

  const { sorted, sortState, handleSort } = useSortable(filtered, COMBINED_COLS, { key: "wa", dir: "desc" });

  const fieldStyle = {
    background: "var(--color-surface-2)",
    border: "1px solid transparent",
    color: "var(--color-ink)",
    fontFamily: "var(--font-body)",
  } as const;

  return (
    <div>
      {/* Controls — same as the per-type view */}
      <div className="flex flex-wrap gap-3 mb-6">
        <select
          value={genreFilter}
          onChange={(e) => setGenreFilter(e.target.value)}
          className="px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={fieldStyle}
        >
          <option value="All genres">All genres</option>
          {genres.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
        <div className="relative flex-1 min-w-52">
          <input
            type="text"
            placeholder="Search by title or author…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
            style={fieldStyle}
          />
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5"
            style={{ color: "var(--color-faint)" }}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
        <div
          className="flex items-center px-3 rounded-lg text-sm font-medium"
          style={{ background: "var(--color-sage-light)", color: "var(--color-sage)" }}
        >
          {filtered.length} {genreFilter !== "All genres" ? `in ${genreFilter}` : "books"}
        </div>
      </div>

      <BookMobileList
        items={sorted}
        cols={COMBINED_COLS}
        sortRows={sorted}
        sortState={sortState}
        onSort={handleSort}
        primaryKey="wa"
        expandedKey={expandedKey}
        setExpandedKey={setExpandedKey}
        onRefresh={onRefresh}
        refreshing={refreshing}
        typeLabel
      />
    </div>
  );
}

/* ── Rankings view (wrapper) ──────────────────────────────────────────────
   Owns the page title and the Fiction / Nonfiction / All type toggle. "All"
   shows the cross-type Total-Average ranking; a single type shows the full
   per-track table. Default is All (seeded from ?type= on a redirect). */

export default function RankingsView({
  fiction,
  nonfiction,
  combined,
  initialType = "all",
  embedded = false,
}: {
  fiction: BooksResponse;
  nonfiction: BooksResponse;
  combined: CombinedRankRow[];
  initialType?: TypeScope;
  // When true, drop the standalone "Rankings" <h1> — the host page (the merged
  // /stats page) supplies its own section heading above this view.
  embedded?: boolean;
}) {
  const [type, setType] = useState<TypeScope>(initialType);
  const isNon = type === "nonfiction";
  return (
    <div>
      {!embedded && (
        <h1
          className="font-display text-3xl font-bold leading-tight mb-4"
          style={{ color: "var(--color-ink)" }}
        >
          Rankings
        </h1>
      )}
      <TypeToggle value={type} onChange={setType} includeAll />
      {type === "all" ? (
        <CombinedRankings rows={combined} fiction={fiction} nonfiction={nonfiction} />
      ) : (
        <PerTypeRankings data={isNon ? nonfiction : fiction} kind={isNon ? "nonfiction" : "fiction"} />
      )}
    </div>
  );
}
