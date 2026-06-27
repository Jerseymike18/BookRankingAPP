"use client";

import { useState, useMemo, useCallback } from "react";
import type { BooksResponse, Book } from "@/lib/types";

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

function formatWA(wa: number) {
  return wa.toFixed(2);
}

function formatWords(words: number | null) {
  if (!words) return null;
  if (words >= 1_000_000) return `${(words / 1_000_000).toFixed(1)}M words`;
  if (words >= 1_000) return `${Math.round(words / 1_000)}K words`;
  return `${words} words`;
}

/* ── Sub-components ───────────────────────────────────────────────────── */

function ComponentGrid({
  components,
  categoryOrder,
}: {
  components: Book["components"];
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

function BookCard({
  book,
  categoryOrder,
  rank,
}: {
  book: Book;
  categoryOrder: string[];
  rank: number;
}) {
  const [open, setOpen] = useState(false);

  return (
    <article
      className={`book-card ${spineClass(book.wa)} px-5 py-4 shadow-sm`}
      style={{ border: "1px solid var(--color-rule)", borderLeftWidth: "3px" }}
    >
      <div
        className="flex items-center gap-4 cursor-pointer select-none"
        onClick={() => setOpen((o) => !o)}
      >
        {/* Rank */}
        <span
          className="text-sm font-display italic flex-shrink-0 w-8 text-right"
          style={{ color: "var(--color-faint)" }}
        >
          {rank}
        </span>

        {/* WA badge */}
        <div className="wa-badge flex-shrink-0">{formatWA(book.wa)}</div>

        {/* Title / author / meta */}
        <div className="flex-1 min-w-0">
          <h3
            className="font-display font-semibold text-base leading-tight truncate"
            style={{ color: "var(--color-ink)" }}
          >
            {book.title}
          </h3>
          <p className="text-sm mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
            {book.author}
            {book.series ? (
              <span style={{ color: "var(--color-faint)" }}>
                {" "}· {book.series}
              </span>
            ) : null}
          </p>
        </div>

        {/* Genre + word count (right side) */}
        <div className="hidden sm:flex flex-col items-end gap-1 flex-shrink-0">
          <span className="genre-chip">{book.genre}</span>
          {book.words ? (
            <span className="text-xs" style={{ color: "var(--color-faint)" }}>
              {formatWords(book.words)}
            </span>
          ) : null}
        </div>

        {/* Expand chevron */}
        <svg
          className="w-4 h-4 flex-shrink-0 transition-transform duration-200"
          style={{
            color: "var(--color-faint)",
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
          }}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Expandable component breakdown */}
      {open && (
        <div
          className="mt-4 pt-4"
          style={{ borderTop: "1px solid var(--color-rule)" }}
        >
          {/* Show genre on mobile when expanded */}
          <div className="flex items-center gap-2 mb-3 sm:hidden">
            <span className="genre-chip">{book.genre}</span>
            {book.words && (
              <span className="text-xs" style={{ color: "var(--color-faint)" }}>
                {formatWords(book.words)}
              </span>
            )}
          </div>
          <ComponentGrid
            components={book.components}
            categoryOrder={categoryOrder}
          />
        </div>
      )}
    </article>
  );
}

/* ── Main rankings view ───────────────────────────────────────────────── */

export default function RankingsClient({ data }: { data: BooksResponse }) {
  const { books, genres, category_order } = data;

  const [genreFilter, setGenreFilter] = useState<string>("All genres");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    let list = books;
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
  }, [books, genreFilter, search]);

  // Re-number ranks within the current view
  const ranked = useMemo(
    () => filtered.map((b, i) => ({ book: b, rank: i + 1 })),
    [filtered]
  );

  const handleGenre = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    setGenreFilter(e.target.value);
  }, []);

  const handleSearch = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
  }, []);

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Rankings
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {books.length} books rated · sorted by Weighted Average
        </p>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap gap-3 mb-6">
        {/* Genre filter */}
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

        {/* Search */}
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

        {/* Count badge */}
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

      {/* Legend for spine colours */}
      <div
        className="flex flex-wrap gap-3 mb-6 p-3 rounded-xl text-xs"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <span style={{ color: "var(--color-muted)" }} className="font-medium self-center">
          Spine:
        </span>
        {[
          { label: "S+ ≥9.5", cls: "spine-sp" },
          { label: "S ≥8.5", cls: "spine-s" },
          { label: "A ≥7.5", cls: "spine-a" },
          { label: "B ≥6.5", cls: "spine-b" },
          { label: "C ≥5.5", cls: "spine-c" },
          { label: "D ≥4.5", cls: "spine-d" },
          { label: "F <4.5", cls: "spine-f" },
        ].map(({ label, cls }) => (
          <span key={cls} className="flex items-center gap-1.5">
            <span
              className={`inline-block w-2.5 h-4 rounded-sm book-card ${cls}`}
              style={{ border: "1px solid transparent", borderLeftWidth: "3px" }}
            />
            <span style={{ color: "var(--color-muted)" }}>{label}</span>
          </span>
        ))}
      </div>

      {/* Book list */}
      <div className="space-y-2">
        {ranked.length === 0 ? (
          <p
            className="text-center py-16 text-sm"
            style={{ color: "var(--color-muted)" }}
          >
            No books match your filters.
          </p>
        ) : (
          ranked.map(({ book, rank }) => (
            <BookCard
              key={book.title}
              book={book}
              categoryOrder={category_order}
              rank={rank}
            />
          ))
        )}
      </div>
    </div>
  );
}
