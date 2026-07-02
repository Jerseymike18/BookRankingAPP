"use client";

import { useState, useMemo } from "react";
import type { ReadingStatsResponse, ReadingStatusResponse, StatusSlot, PerYearRow, GenreRow, AuthorRow, BookKind, Book } from "@/lib/types";
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";
import { seriesLabel } from "@/lib/format";
import { CATEGORIES, categoryAverages, type Category } from "@/lib/analytics";

/* ── Sub-tab bar ──────────────────────────────────────────────────────────── */

type Tab = "stats" | "status";

function SubTabs({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "stats", label: "Stats" },
    { id: "status", label: "Status" },
  ];
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

/* ── Stat card ────────────────────────────────────────────────────────────── */

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-1"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
        {label}
      </span>
      <span className="font-display text-2xl font-bold" style={{ color: "var(--color-ink)" }}>
        {value}
      </span>
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-display text-lg font-semibold mt-8 mb-3" style={{ color: "var(--color-ink)" }}>
      {children}
    </h3>
  );
}

function fmtWords(w: number | null) {
  if (!w) return "—";
  if (w >= 1_000_000) return `${(w / 1_000_000).toFixed(1)}M`;
  if (w >= 1_000) return `${Math.round(w / 1_000)}K`;
  return `${w}`;
}

const PER_YEAR_COLS: ColDef<PerYearRow>[] = [
  { key: "year",              label: "Year",          type: "numeric", getValue: (r) => r.year,              align: "left" },
  { key: "books",             label: "Books",         type: "numeric", getValue: (r) => r.books },
  { key: "avg_wa",            label: "Avg WA",        type: "numeric", getValue: (r) => r.avg_wa,            formatter: (v) => v != null ? Number(v).toFixed(2) : "—" },
  { key: "avg_total_average", label: "Avg Total Avg", type: "numeric", getValue: (r) => r.avg_total_average, formatter: (v) => v != null ? Number(v).toFixed(2) : "—" },
  { key: "avg_words",         label: "Avg Words",     type: "numeric", getValue: (r) => r.avg_words,         formatter: (v) => fmtWords(v as number | null) },
];

const BY_GENRE_COLS: ColDef<GenreRow>[] = [
  { key: "genre",             label: "Genre",         type: "string",  getValue: (r) => r.genre },
  { key: "books",             label: "Books",         type: "numeric", getValue: (r) => r.books },
  { key: "avg_wa",            label: "Avg WA",        type: "numeric", getValue: (r) => r.avg_wa,            formatter: (v) => v != null ? Number(v).toFixed(2) : "—" },
  { key: "avg_total_average", label: "Avg Total Avg", type: "numeric", getValue: (r) => r.avg_total_average, formatter: (v) => v != null ? Number(v).toFixed(2) : "—" },
  { key: "avg_words",         label: "Avg Words",     type: "numeric", getValue: (r) => r.avg_words,         formatter: (v) => fmtWords(v as number | null) },
];

const BY_AUTHOR_COLS: ColDef<AuthorRow>[] = [
  { key: "author", label: "Author", type: "string",  getValue: (r) => r.author },
  { key: "books",  label: "Books",  type: "numeric", getValue: (r) => r.books },
  { key: "avg_wa", label: "Avg WA", type: "numeric", getValue: (r) => r.avg_wa, formatter: (v) => v != null ? Number(v).toFixed(2) : "—" },
];

/* ── Category-average columns (fiction only, derived client-side from books) ──
   The reading-stats endpoint (views.reading_stats) is read-only and doesn't
   carry per-category averages, so we compute them here from the live /api/books
   payload — the same source of truth, grouped by genre / author. */

const CAT_ABBR: Record<Category, string> = {
  Story: "Story", Character: "Char", Aesthetics: "Aes", Theme: "Theme", Worldbuilding: "WB",
};

function groupByKey<T>(items: T[], keyOf: (t: T) => string): Map<string, T[]> {
  const m = new Map<string, T[]>();
  for (const it of items) {
    const k = keyOf(it);
    const arr = m.get(k);
    if (arr) arr.push(it);
    else m.set(k, [it]);
  }
  return m;
}

/** One ColDef per category, reading the pre-grouped averages by row key. */
function catCols<T>(
  keyOf: (r: T) => string,
  lookup: Map<string, Record<Category, number | null>>,
): ColDef<T>[] {
  return CATEGORIES.map((cat): ColDef<T> => ({
    key: `cat_${cat}`,
    label: CAT_ABBR[cat],
    type: "numeric",
    getValue: (r) => lookup.get(keyOf(r))?.[cat] ?? null,
    formatter: (v) => (v != null ? Number(v).toFixed(2) : "—"),
  }));
}

/* ── Stats tab ────────────────────────────────────────────────────────────── */

function StatsTab({ stats, kind, books }: { stats: ReadingStatsResponse; kind: BookKind; books?: Book[] }) {
  const { summary, per_year, by_genre, by_author } = stats;

  // Fiction gets per-category averages (Story/Char/Aes/Theme/WB) folded into the
  // by-genre and by-author tables, computed live from the books payload.
  const hasCats = kind === "fiction" && !!books && books.length > 0;

  const { genreCols, authorCols } = useMemo(() => {
    if (!hasCats || !books) return { genreCols: BY_GENRE_COLS, authorCols: BY_AUTHOR_COLS };
    const genreAvgs = new Map(
      [...groupByKey(books, (b) => b.genre)].map(([k, bs]) => [k, categoryAverages(bs)] as const)
    );
    const authorAvgs = new Map(
      [...groupByKey(books, (b) => b.author)].map(([k, bs]) => [k, categoryAverages(bs)] as const)
    );
    return {
      // Genre: slot the 5 category columns in just before the trailing "Avg Words".
      genreCols: [
        ...BY_GENRE_COLS.slice(0, -1),
        ...catCols<GenreRow>((r) => r.genre, genreAvgs),
        BY_GENRE_COLS[BY_GENRE_COLS.length - 1],
      ],
      // Author: append the 5 category columns after "Avg WA".
      authorCols: [...BY_AUTHOR_COLS, ...catCols<AuthorRow>((r) => r.author, authorAvgs)],
    };
  }, [hasCats, books]);

  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-2">
        <StatCard label="Total books" value={`${summary.total_books}`} />
        <StatCard label="Avg WA" value={summary.avg_wa != null ? summary.avg_wa.toFixed(2) : "—"} />
        <StatCard label="Avg Total Avg" value={summary.avg_total_average != null ? summary.avg_total_average.toFixed(2) : "—"} />
        <StatCard label="Avg word count" value={fmtWords(summary.avg_words)} />
      </div>

      <SectionHeading>Per year</SectionHeading>
      <SortableTable
        columns={PER_YEAR_COLS}
        data={per_year}
        defaultSort={{ key: "year", dir: "desc" }}
        getRowKey={(r) => String(r.year)}
      />

      {kind === "fiction" && (
        <>
          <SectionHeading>By genre</SectionHeading>
          <SortableTable
            columns={genreCols}
            data={by_genre}
            defaultSort={{ key: "books", dir: "desc" }}
            getRowKey={(r) => r.genre}
            scrollX={hasCats}
          />
          {hasCats && (
            <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
              Story, Char, Aes, Theme, and WB are the average weighted score per category (0–10).
              WB (Worldbuilding) is averaged over only the books that scored it, so realist genres show “—”.
            </p>
          )}
        </>
      )}

      <SectionHeading>By author</SectionHeading>
      <SortableTable
        columns={authorCols}
        data={by_author}
        defaultSort={{ key: "books", dir: "desc" }}
        getRowKey={(r) => r.author}
        scrollX={hasCats}
      />
    </div>
  );
}

/* ── Status tab ───────────────────────────────────────────────────────────── */

const CATEGORY_ORDER_BY_KIND: Record<BookKind, string[]> = {
  fiction: ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"],
  nonfiction: ["Quality", "Aesthetics", "Theme"],
};

function SlotCard({
  label,
  slot,
  predicted,
  kind,
}: {
  label: string;
  slot: StatusSlot | null;
  predicted: boolean;
  kind: BookKind;
}) {
  const cardStyle = {
    background: "var(--color-surface)",
    border: "1px solid var(--color-rule)",
  };

  if (!slot) {
    return (
      <div className="rounded-xl p-5 flex flex-col gap-2" style={cardStyle}>
        <span
          className="text-xs font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-muted)" }}
        >
          {label}
        </span>
        <p className="text-sm mt-2" style={{ color: "var(--color-faint)" }}>
          —
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl p-5 flex flex-col gap-3" style={cardStyle}>
      {/* Label row */}
      <div className="flex items-center gap-2">
        <span
          className="text-xs font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-muted)" }}
        >
          {label}
        </span>
        {predicted && (
          <span
            className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{
              background: "var(--color-surface-2)",
              color: "var(--color-sage)",
              border: "1px solid var(--color-rule)",
            }}
          >
            predicted
          </span>
        )}
      </div>

      {/* Title / meta */}
      <div>
        <p
          className="font-display font-bold text-lg leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          {slot.title}
        </p>
        <p className="text-sm mt-0.5" style={{ color: "var(--color-muted)" }}>
          {[slot.author, slot.genre].filter(Boolean).join(" · ")}
        </p>
        {slot.series && (
          <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>
            {seriesLabel(slot.series, slot.series_number)}
          </p>
        )}
      </div>

      {/* Score section */}
      {slot.has_prediction === false && slot.wa === null ? (
        <p className="text-sm" style={{ color: "var(--color-faint)" }}>
          No prediction yet — visit the{" "}
          <a
            href="/predict"
            className="underline"
            style={{ color: "var(--color-sage)" }}
          >
            Predict page
          </a>{" "}
          to research this book.
        </p>
      ) : (
        <>
          {/* WA badge + rank */}
          <div className="flex items-center gap-3">
            <span className="wa-badge">{slot.wa?.toFixed(2)}</span>
            {slot.rank != null && (
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                rank {slot.rank} of {slot.total}
              </span>
            )}
          </div>

          {/* Category averages */}
          {Object.keys(slot.category_avgs).length > 0 && (
            <div className="grid grid-cols-3 gap-1.5 mt-1">
              {CATEGORY_ORDER_BY_KIND[kind].filter((cat) => cat in slot.category_avgs).map((cat) => (
                <div key={cat} className="comp-tile">
                  <span className="comp-label">{cat}</span>
                  <span className="comp-value">
                    {slot.category_avgs[cat].toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatusTab({ status, kind }: { status: ReadingStatusResponse; kind: BookKind }) {
  return (
    <div className="grid sm:grid-cols-3 gap-4">
      <SlotCard label="Last Read" slot={status.last_read} predicted={false} kind={kind} />
      <SlotCard
        label="Currently Reading"
        slot={status.currently_reading}
        predicted={true}
        kind={kind}
      />
      <SlotCard label="Reading Next" slot={status.reading_next} predicted={true} kind={kind} />
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────────────────────── */

export default function ReadingView({
  stats,
  status,
  kind = "fiction",
  books,
}: {
  stats: ReadingStatsResponse;
  status: ReadingStatusResponse;
  kind?: BookKind;
  /** Fiction only: live books payload, used to derive per-category averages. */
  books?: Book[];
}) {
  const [tab, setTab] = useState<Tab>("stats");

  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Reading
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {stats.summary.total_books} books rated
        </p>
      </div>

      <SubTabs active={tab} onChange={setTab} />

      {tab === "stats" ? (
        <StatsTab stats={stats} kind={kind} books={books} />
      ) : (
        <StatusTab status={status} kind={kind} />
      )}
    </div>
  );
}
