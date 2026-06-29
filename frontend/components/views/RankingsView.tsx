"use client";

import React, { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { editRating, deleteBook } from "@/lib/api";
import type { BooksResponse, Book, CategoryComponents, BookKind } from "@/lib/types";
import { seriesLabel } from "@/lib/format";
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

function flattenComponents(components: CategoryComponents): Record<string, number> {
  const flat: Record<string, number> = {};
  for (const comps of Object.values(components)) {
    for (const [comp, val] of Object.entries(comps)) {
      flat[comp] = val ?? 0;
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

function ScoreGrid({
  components,
  categoryOrder,
  scores,
  onChange,
}: {
  components: CategoryComponents;
  categoryOrder: string[];
  scores: Record<string, number>;
  onChange: (comp: string, val: number) => void;
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
                    value={scores[comp] ?? 0}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!isNaN(v)) onChange(comp, v);
                    }}
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

/* ── Expanded book panel ─────────────────────────────────────────────── */

type CardMode = "view" | "edit" | "confirm-delete";

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
  const [scores, setScores] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  function enterEdit() {
    setScores(flattenComponents(book.components));
    setMode("edit");
    setActionError(null);
    setSaveSuccess(false);
  }

  function cancelEdit() {
    setMode("view");
    setActionError(null);
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      await editRating(book.title, scores, kind);
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
          <div className="flex gap-3 mt-5">
            <button
              onClick={enterEdit}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              style={{ background: "var(--color-sage)", color: "#fff" }}
            >
              Edit scores
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

type YearTab = "all" | "2026" | "2025";

const YEAR_TABS: { id: YearTab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "2026", label: "2026" },
  { id: "2025", label: "2025" },
];

function SubTabs({
  active,
  onChange,
}: {
  active: YearTab;
  onChange: (t: YearTab) => void;
}) {
  return (
    <div
      className="flex gap-1 mb-6 p-1 rounded-xl inline-flex"
      style={{ background: "var(--color-surface-2)" }}
    >
      {YEAR_TABS.map(({ id, label }) => (
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

      {/* Year sub-tabs (fiction only — nonfiction books have no year_read) */}
      {kind === "fiction" && <SubTabs active={yearTab} onChange={handleYearTab} />}

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
