"use client";

import { useState, useMemo, Fragment } from "react";
import type { SeriesResponse, SeriesEntry, SeriesTiersResponse, SeriesTierEntry, BookKind } from "@/lib/types";
import { TierLadder } from "@/components/TierLadder";
import { TypeToggle, type TypeScope } from "@/components/TypeToggle";
import { useSortable, SortableTh } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";
import { setSeriesComplete } from "@/lib/api";
import { useReadOnly } from "@/lib/readonly-context";

/* ── Sub-tab bar ──────────────────────────────────────────────────────────── */

type Tab = "rankings" | "tiers";

function SubTabs({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "rankings", label: "Series Rankings" },
    { id: "tiers", label: "Series Tier List" },
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

/* ── Genre filter ─────────────────────────────────────────────────────────── */

function GenreFilter({
  genres,
  active,
  onChange,
}: {
  genres: string[];
  active: string;
  onChange: (g: string) => void;
}) {
  return (
    <select
      value={active}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg px-3 py-2 text-sm mb-4"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-rule)",
        color: "var(--color-ink)",
      }}
    >
      <option value="">All genres</option>
      {genres.map((g) => (
        <option key={g} value={g}>{g}</option>
      ))}
    </select>
  );
}

/* ── Column definitions ───────────────────────────────────────────────────── */

/** Total series-quality adjustment: how far the series score sits from the plain
 *  average of its books. This is the whole point of the model, so it gets a
 *  sortable column of its own — sort by it to see which series are carried (or
 *  sunk) by how they're BUILT rather than by how good the individual books are. */
const adjustment = (s: SeriesEntry | SeriesTierEntry) =>
  (s.adjusted_wa ?? 0) - (s.avg_wa ?? 0);

const SERIES_COLS: ColDef<SeriesEntry>[] = [
  { key: "series",      label: "Series",  type: "string",  getValue: (s) => s.series,      align: "left"  },
  { key: "author",      label: "Author",  type: "string",  getValue: (s) => s.author,      align: "left"  },
  { key: "genre",       label: "Genre",   type: "string",  getValue: (s) => s.genre,       align: "left"  },
  { key: "books",       label: "Books",   type: "numeric", getValue: (s) => s.books,       align: "right" },
  { key: "adjusted_wa", label: "Score",   type: "numeric", getValue: (s) => s.adjusted_wa, align: "right" },
  { key: "avg_wa",      label: "Avg WA",  type: "numeric", getValue: (s) => s.avg_wa,      align: "right" },
  { key: "adjustment",  label: "±",       type: "numeric", getValue: adjustment,           align: "right" },
];

/* ── Score breakdown ──────────────────────────────────────────────────────── */

const signed = (v: number | null | undefined, digits = 3) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;

/** One term of the score: its contribution, and the raw deviation it came from
 *  (so the number is explained, not just displayed). */
function Term({
  label,
  value,
  detail,
}: {
  label: string;
  value: number | null | undefined;
  detail: string;
}) {
  const on = value != null && Math.abs(value) >= 0.0005;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>
          {label}
        </span>
        <span
          className="text-sm font-semibold"
          style={{
            /* Same up/down convention the Track Record table uses; the down
               colour is the existing --color-spine-c token, not a new value. */
            color: !on ? "var(--color-faint)" : value! > 0 ? "var(--color-sage)" : "var(--color-spine-c)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {signed(value)}
        </span>
      </div>
      <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>{detail}</p>
    </div>
  );
}

/** The expanded panel under a series row: every term, plus the finished/ongoing
 *  switch that licenses the Finale term. */
function ScoreBreakdown({
  s,
  onToggleComplete,
  pending,
  error,
}: {
  s: SeriesEntry;
  onToggleComplete: () => void;
  pending: boolean;
  error: string | null;
}) {
  const readOnly = useReadOnly();
  const complete = s.complete === true;
  const solo = s.books < 2;

  return (
    <div className="px-3 py-4" style={{ background: "var(--color-surface-2)" }}>
      <div className="grid gap-4 mb-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
        <Term
          label="Consistency"
          value={s.consistency}
          detail={
            solo
              ? "needs 2+ books"
              : s.weakest_pct == null
              ? "no library reference"
              : `its weakest book still beats ${Math.round(s.weakest_pct * 100)}% of everything you've read`
          }
        />
        <Term
          label="Peak"
          value={s.peak}
          detail={
            solo
              ? "needs 2+ books"
              : `best volume rises ${s.peak_lift?.toFixed(2) ?? "—"} WA above its own average`
          }
        />
        <Term
          label="Finale"
          value={s.finale}
          detail={
            solo
              ? "needs 2+ books"
              : !complete
              ? "not counted — series not marked finished"
              : `last book's Ending is ${signed(s.finale_lift, 2)} against the series' own average ending`
          }
        />
        <Term
          label="Evidence"
          value={s.evidence}
          detail={
            solo
              ? "one book is nothing to be consistent about — held back until you read another"
              : `${s.books} books is enough to judge`
          }
        />
      </div>

      <div className="flex items-center flex-wrap gap-3 pt-3" style={{ borderTop: "1px solid var(--color-rule)" }}>
        <span className="text-xs" style={{ color: "var(--color-muted)" }}>
          {complete ? "Marked finished" : "Marked ongoing"} — only a finished series is scored on its ending.
        </span>
        {!readOnly && (
          <button
            onClick={onToggleComplete}
            disabled={pending}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-rule)",
              color: "var(--color-sage)",
              opacity: pending ? 0.6 : 1,
              cursor: pending ? "default" : "pointer",
            }}
          >
            {pending ? "Saving…" : complete ? "Mark as ongoing" : "Mark as finished"}
          </button>
        )}
        {error && (
          <span className="text-xs" style={{ color: "#B91C1C" }}>{error}</span>
        )}
      </div>
    </div>
  );
}

/* ── Rankings tab ─────────────────────────────────────────────────────────── */

function RankingsTab({
  data,
  emptyMsg,
  kind,
}: {
  data: SeriesResponse;
  emptyMsg: string;
  kind: BookKind;
}) {
  const [genre, setGenre] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<{ series: string; message: string } | null>(null);
  // Completeness flips optimistically here; a reload re-reads it from the server.
  const [completeOverride, setCompleteOverride] = useState<Record<string, boolean>>({});

  // Nonfiction series have no quality model, so there is nothing to break down.
  const scored = kind === "fiction";

  const genres = useMemo(
    () => [...new Set(data.series.map((s) => s.genre))].sort(),
    [data.series]
  );

  const filtered = useMemo(
    () => (genre ? data.series.filter((s) => s.genre === genre) : data.series),
    [data.series, genre]
  );

  const cols = useMemo(
    () => (scored ? SERIES_COLS : SERIES_COLS.filter((c) => c.key !== "adjustment")),
    [scored]
  );

  const { sorted, sortState, handleSort } = useSortable(
    filtered,
    cols,
    { key: "adjusted_wa", dir: "desc" }
  );

  async function toggleComplete(s: SeriesEntry) {
    const next = !(completeOverride[s.series] ?? s.complete === true);
    setPending(s.series);
    setError(null);
    try {
      await setSeriesComplete(s.series, next);
      setCompleteOverride((m) => ({ ...m, [s.series]: next }));
    } catch (e) {
      setError({
        series: s.series,
        message: e instanceof Error ? e.message : "Could not save.",
      });
    } finally {
      setPending(null);
    }
  }

  if (data.series.length === 0) {
    return <p className="text-sm" style={{ color: "var(--color-muted)" }}>{emptyMsg}</p>;
  }

  return (
    <div>
      <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
        {scored ? (
          <>
            Ranked by a series score — the average WA of its books, adjusted for the
            things an average can&apos;t see: whether even its weakest volume is
            excellent (Consistency), whether it produced a standout (Peak), and
            whether it stuck the landing (Finale). Length earns nothing on its own.
            Click a series to see its breakdown; click a column header to sort.
          </>
        ) : (
          <>Ranked by average WA with a length adjustment. Click a column header to sort.</>
        )}
      </p>
      <GenreFilter genres={genres} active={genre} onChange={setGenre} />
      <p className="text-xs mb-3" style={{ color: "var(--color-faint)" }}>{sorted.length} series</p>
      <div style={{ overflowX: "auto" }}>
        <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "var(--color-surface-2)", borderBottom: "1px solid var(--color-rule)" }}>
              <th className="px-3 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)" }}>#</th>
              {cols.map((col) => (
                <SortableTh key={col.key} col={col} sortState={sortState} onSort={handleSort} />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((s, i) => {
              const expanded = scored && open === s.series;
              const row = { ...s, complete: completeOverride[s.series] ?? s.complete };
              const adj = adjustment(s);
              return (
              <Fragment key={s.series}>
              <tr
                onClick={scored ? () => setOpen(expanded ? null : s.series) : undefined}
                style={{
                  borderTop: i === 0 ? "none" : "1px solid var(--color-rule)",
                  cursor: scored ? "pointer" : "default",
                  background: expanded ? "var(--color-sage-light)" : "transparent",
                }}
              >
                <td className="px-3 py-2.5 text-xs" style={{ color: "var(--color-faint)" }}>{i + 1}</td>
                <td className="px-3 py-2.5 font-semibold font-display" style={{ color: "var(--color-ink)", background: !expanded && sortState.key === "series" ? "var(--color-sage-light)" : "transparent" }}>
                  {s.series}
                  {scored && row.complete === true && (
                    <span className="ml-2 text-xs font-normal font-sans" style={{ color: "var(--color-faint)" }}>finished</span>
                  )}
                </td>
                <td className="px-3 py-2.5" style={{ color: "var(--color-muted)", background: !expanded && sortState.key === "author" ? "var(--color-sage-light)" : "transparent" }}>{s.author}</td>
                <td className="px-3 py-2.5" style={{ background: !expanded && sortState.key === "genre" ? "var(--color-sage-light)" : "transparent" }}>
                  <span className="genre-chip">{s.genre}</span>
                </td>
                <td className="px-3 py-2.5 text-right" style={{ color: sortState.key === "books" ? "var(--color-sage)" : "var(--color-muted)", background: !expanded && sortState.key === "books" ? "var(--color-sage-light)" : "transparent" }}>{s.books}</td>
                <td className="px-3 py-2.5 text-right font-semibold" style={{ color: "var(--color-sage)", background: !expanded && sortState.key === "adjusted_wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{s.adjusted_wa?.toFixed(3) ?? "—"}</td>
                <td className="px-3 py-2.5 text-right" style={{ color: sortState.key === "avg_wa" ? "var(--color-sage)" : "var(--color-ink)", background: !expanded && sortState.key === "avg_wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{s.avg_wa?.toFixed(2) ?? "—"}</td>
                {scored && (
                  <td className="px-3 py-2.5 text-right" style={{ color: Math.abs(adj) < 0.0005 ? "var(--color-faint)" : adj > 0 ? "var(--color-sage)" : "var(--color-spine-c)", background: !expanded && sortState.key === "adjustment" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>{signed(adj)}</td>
                )}
              </tr>
              {expanded && (
                <tr>
                  <td colSpan={cols.length + 1} style={{ padding: 0 }}>
                    <ScoreBreakdown
                      s={row}
                      onToggleComplete={() => toggleComplete(s)}
                      pending={pending === s.series}
                      error={error?.series === s.series ? error.message : null}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Tiers tab ────────────────────────────────────────────────────────────── */

function TiersTab({ data, emptyMsg }: { data: SeriesTiersResponse; emptyMsg: string }) {
  const [genre, setGenre] = useState("");

  const genres = useMemo(
    () => [...new Set(data.series.map((s) => s.genre))].sort(),
    [data.series]
  );

  const itemsByTier = useMemo(() => {
    const filtered = genre ? data.series.filter((s) => s.genre === genre) : data.series;
    const map: Record<string, { label: string }[]> = {};
    for (const t of data.tier_order) map[t] = [];
    for (const t of data.tier_order) {
      const entries = filtered
        .filter((s: SeriesTierEntry) => s.tier === t)
        .sort((a: SeriesTierEntry, b: SeriesTierEntry) => (b.adjusted_wa ?? 0) - (a.adjusted_wa ?? 0));
      map[t] = entries.map((s: SeriesTierEntry) => ({ label: s.series }));
    }
    return map;
  }, [data, genre]);

  if (data.series.length === 0) {
    return <p className="text-sm" style={{ color: "var(--color-muted)" }}>{emptyMsg}</p>;
  }

  const visibleCount = Object.values(itemsByTier).reduce((n, arr) => n + arr.length, 0);

  const summaryLine = data.tier_order
    .filter((t) => (itemsByTier[t]?.length ?? 0) > 0)
    .map((t) => `${t}: ${itemsByTier[t].length}`)
    .join("  ·  ");

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <GenreFilter genres={genres} active={genre} onChange={setGenre} />
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--color-muted)" }}>
        {visibleCount} series{summaryLine ? ` · ${summaryLine}` : ""}
      </p>
      <TierLadder tierOrder={data.tier_order} itemsByTier={itemsByTier} />
    </div>
  );
}

/* ── Single-track series view ─────────────────────────────────────────────── */

function SeriesSingle({
  seriesData,
  tiersData,
  kind = "fiction",
}: {
  seriesData: SeriesResponse;
  tiersData: SeriesTiersResponse;
  kind?: BookKind;
}) {
  const [tab, setTab] = useState<Tab>("rankings");
  const emptyMsg =
    kind === "nonfiction"
      ? "No nonfiction series yet."
      : "No multi-book series found.";

  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Series
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {seriesData.series.length} series tracked
        </p>
      </div>

      <SubTabs active={tab} onChange={setTab} />

      {tab === "rankings" ? (
        <RankingsTab data={seriesData} emptyMsg={emptyMsg} kind={kind} />
      ) : (
        <TiersTab data={tiersData} emptyMsg={emptyMsg} />
      )}
    </div>
  );
}

/* ── Series view (wrapper) ────────────────────────────────────────────────
   Fiction / Nonfiction toggle only — no "All". The headline number is the series
   score (WA-scaled) and the tier tab is banded within a single track, so the two
   tracks can't share one ordering. Default is Fiction (seeded from ?type=).

   Only the FICTION track has the series-quality model (Commitment/Peak/Floor/
   Finale); the nonfiction routes return a plain length-adjusted average, so its
   rows carry no term fields and the breakdown UI is switched off for it rather
   than shown with fabricated zeroes. */

export default function SeriesView({
  fiction,
  nonfiction,
  initialType = "fiction",
}: {
  fiction: { seriesData: SeriesResponse; tiersData: SeriesTiersResponse };
  nonfiction: { seriesData: SeriesResponse; tiersData: SeriesTiersResponse };
  initialType?: TypeScope;
}) {
  const [type, setType] = useState<TypeScope>(initialType === "nonfiction" ? "nonfiction" : "fiction");
  const isNon = type === "nonfiction";
  const active = isNon ? nonfiction : fiction;
  return (
    <div>
      <TypeToggle value={isNon ? "nonfiction" : "fiction"} onChange={setType} includeAll={false} />
      <SeriesSingle
        seriesData={active.seriesData}
        tiersData={active.tiersData}
        kind={isNon ? "nonfiction" : "fiction"}
      />
    </div>
  );
}
