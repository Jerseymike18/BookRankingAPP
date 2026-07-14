"use client";

import React, { useState, useMemo, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { editRating, deleteBook, fetchValidGenres, updateBookMetadata } from "@/lib/api";
import type { BookMetadataPayload } from "@/lib/api";
import type { BooksResponse, Book, CategoryComponents, BookKind } from "@/lib/types";
import { seriesLabel } from "@/lib/format";
import { READONLY } from "@/lib/readonly";
import { useSortable, SortableTh } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";

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

function formatWords(words: number | null) {
  if (!words) return null;
  if (words >= 1_000_000) return `${(words / 1_000_000).toFixed(1)}M words`;
  if (words >= 1_000) return `${Math.round(words / 1_000)}K words`;
  return `${words} words`;
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
}: {
  components: CategoryComponents;
  categoryOrder: string[];
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
                  <span className="comp-label">{comp}</span>
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
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
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
}: {
  components: CategoryComponents;
  categoryOrder: string[];
  scores: Record<string, string>;
  onChange: (comp: string, val: string) => void;
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
                    {comp}
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
          <p className="text-xs mt-1" style={{ color: "var(--color-clay, #C07C5A)" }}>
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
  onClose,
}: {
  book: Book;
  categoryOrder: string[];
  kind: BookKind;
  onRefresh: () => void;
  onClose: () => void;
}) {
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
          style={{ background: "#FEF2F2", border: "1px solid #FCA5A5" }}
        >
          <p className="text-sm font-semibold mb-1" style={{ color: "#B91C1C" }}>
            Delete &ldquo;{book.title}&rdquo;?
          </p>
          <p className="text-sm mb-4" style={{ color: "#7F1D1D" }}>
            This permanently removes it from your library and all stats and rankings.
          </p>
          <div className="flex gap-3">
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
              style={{ background: "#DC2626", color: "#fff" }}
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
          style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}
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
          <ComponentGrid components={book.components} categoryOrder={categoryOrder} />
          {!READONLY && (
          <div className="flex gap-3 mt-5">
            <button
              onClick={enterEdit}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{ background: "var(--color-sage)", color: "#fff" }}
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
                color: "#DC2626",
                border: "1px solid #FCA5A5",
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
            onChange={(comp, val) =>
              setScores((prev) => ({ ...prev, [comp]: val }))
            }
          />
          <div className="flex gap-3 mt-5">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-sage)", color: "#fff" }}
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
              style={{ background: "var(--color-sage)", color: "#fff" }}
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
        </>
      )}
    </div>
  );
}

/* ── Column definitions ───────────────────────────────────────────────── */

// Short column headers per category (fiction + nonfiction).
const CAT_ABBREV: Record<string, string> = {
  Story: "Story", Character: "Char", Aesthetics: "Aes", Theme: "Theme",
  Worldbuilding: "WB", Quality: "Qual", Phraseology: "Phra",
};

// The primary ranking score: fiction sorts/colours by WA, nonfiction by Total
// Average (the workbook's nonfiction ranking; WA is shown but secondary).
function primaryScore(b: Book, kind: BookKind): number {
  return kind === "nonfiction" ? (b.total_average ?? 0) : b.wa;
}

// Columns are built from the response's category_order (5 for fiction, 3 for
// nonfiction) so the same table serves both types.
function buildCols(kind: BookKind, categoryOrder: string[]): ColDef<Book>[] {
  return [
    { key: "title", label: "Book", type: "string", getValue: (b) => b.title, align: "left" },
    {
      key: kind === "nonfiction" ? "total_average" : "wa",
      label: kind === "nonfiction" ? "Total" : "WA",
      type: "numeric", getValue: (b) => primaryScore(b, kind), align: "right",
    },
    ...categoryOrder.map((cat): ColDef<Book> => ({
      key: cat, label: CAT_ABBREV[cat] ?? cat, type: "numeric",
      getValue: (b) => (b.category_avgs ?? {})[cat] ?? 0, align: "right",
    })),
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
  return (
    <div
      className="flex gap-1 mb-6 p-1 rounded-xl inline-flex"
      style={{ background: "var(--color-surface-2)" }}
    >
      {tabs.map(({ id, label }) => (
        <button
          key={id}
          onClick={() => onChange(id)}
          className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
          style={{
            background: active === id ? "var(--color-surface)" : "transparent",
            color: active === id ? "var(--color-sage)" : "var(--color-muted)",
            boxShadow: active === id ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/* ── Main rankings view ───────────────────────────────────────────────── */

export default function RankingsView({
  data,
  kind = "fiction",
}: {
  data: BooksResponse;
  kind?: BookKind;
}) {
  const { books, genres, category_order } = data;
  const router = useRouter();
  const primaryKey = kind === "nonfiction" ? "total_average" : "wa";
  const cols = useMemo(() => buildCols(kind, category_order), [kind, category_order]);

  const [yearTab, setYearTab] = useState<YearTab>("all");
  const [genreFilter, setGenreFilter] = useState<string>("All genres");
  const [search, setSearch] = useState("");
  const [expandedTitle, setExpandedTitle] = useState<string | null>(null);

  const onRefresh = useCallback(() => router.refresh(), [router]);

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
      {/* Page header */}
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Rankings
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {books.length} books rated · click a column header to sort · click a row to expand scores
        </p>
      </div>

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
            background: "var(--color-surface)",
            border: "1px solid var(--color-rule)",
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
              background: "var(--color-surface)",
              border: "1px solid var(--color-rule)",
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

      {/* Rankings table */}
      <div style={{ overflowX: "auto" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "0.875rem",
          }}
        >
          <thead>
            <tr style={{ background: "var(--color-surface)" }}>
              <th
                className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2"
                style={{
                  color: "var(--color-muted)",
                  borderBottom: "1px solid var(--color-rule)",
                  minWidth: "2rem",
                }}
              >
                #
              </th>
              {cols.map((col) => (
                <SortableTh
                  key={col.key}
                  col={col}
                  sortState={sortState}
                  onSort={handleSort}
                  extraStyle={col.key === "title" ? { minWidth: "12rem" } : undefined}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={10}
                  className="text-center py-16 text-sm"
                  style={{ color: "var(--color-muted)" }}
                >
                  No books match your filters.
                </td>
              </tr>
            ) : (
              sorted.map((book, i) => {
                const isExpanded = expandedTitle === book.title;
                const avgs = book.category_avgs ?? {};
                return (
                  <React.Fragment key={book.title}>
                    <tr
                      onClick={() =>
                        setExpandedTitle(isExpanded ? null : book.title)
                      }
                      className={`book-card ${spineClass(primaryScore(book, kind))} cursor-pointer`}
                      style={{
                        borderBottom: isExpanded
                          ? "none"
                          : "1px solid var(--color-rule)",
                        borderLeft: "3px solid",
                        transition: "background 0.1s",
                      }}
                    >
                      <td
                        className="px-3 py-3 font-display italic text-sm text-right"
                        style={{ color: "var(--color-faint)", minWidth: "2.5rem" }}
                      >
                        {i + 1}
                      </td>
                      <td
                        className="px-3 py-3"
                        style={{
                          minWidth: "12rem",
                          background: sortState.key === "title" ? "var(--color-sage-light)" : "transparent",
                        }}
                      >
                        <div
                          className="font-display font-semibold text-sm leading-tight"
                          style={{ color: "var(--color-ink)" }}
                        >
                          {book.title}
                        </div>
                        <div className="text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
                          {book.author}
                          {book.series ? (
                            <span style={{ color: "var(--color-faint)" }}>
                              {" "}· {seriesLabel(book.series, book.series_number)}
                            </span>
                          ) : null}
                          {book.words ? (
                            <span style={{ color: "var(--color-faint)" }}>
                              {" "}· {formatWords(book.words)}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      {/* Primary score: WA (fiction) or Total Average (nonfiction) */}
                      <td
                        className="px-3 py-3 text-right font-semibold"
                        style={{
                          color: sortState.key === primaryKey ? "var(--color-sage)" : "var(--color-ink)",
                          background: sortState.key === primaryKey ? "var(--color-sage-light)" : "transparent",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {primaryScore(book, kind).toFixed(2)}
                      </td>
                      {/* Category averages */}
                      {category_order.map((cat) => {
                        const val = avgs[cat] ?? 0;
                        const isActive = sortState.key === cat;
                        return (
                          <td
                            key={cat}
                            className="px-3 py-3 text-right"
                            style={{
                              color: val === 0 ? "var(--color-faint)" : (isActive ? "var(--color-sage)" : "var(--color-muted)"),
                              background: isActive ? "var(--color-sage-light)" : "transparent",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {val === 0 ? "—" : val.toFixed(2)}
                          </td>
                        );
                      })}
                      <td
                        className="px-3 py-3"
                        style={{
                          background: sortState.key === "genre" ? "var(--color-sage-light)" : "transparent",
                        }}
                      >
                        <span className="genre-chip">{book.genre}</span>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        <td
                          colSpan={10}
                          style={{ padding: 0, borderBottom: "1px solid var(--color-rule)" }}
                        >
                          <BookExpandedPanel
                            book={book}
                            categoryOrder={category_order}
                            kind={kind}
                            onRefresh={onRefresh}
                            onClose={() => setExpandedTitle(null)}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
