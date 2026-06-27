"use client";

import { useState, useEffect, useTransition } from "react";
import type { ReadingStatsResponse, ReadingStatusResponse } from "@/lib/types";
import { setYearRead } from "@/lib/api";

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

function Table({ headers, rows }: { headers: string[]; rows: (string | number | null)[][] }) {
  return (
    <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--color-rule)" }}>
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: "var(--color-surface-2)" }}>
            {headers.map((h) => (
              <th key={h} className="px-4 py-2.5 text-left font-semibold text-xs uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderTop: i === 0 ? "none" : "1px solid var(--color-rule)" }}>
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-2.5" style={{ color: "var(--color-ink)" }}>
                  {cell ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Stats tab ────────────────────────────────────────────────────────────── */

function StatsTab({ stats }: { stats: ReadingStatsResponse }) {
  const { summary, per_year, by_genre, by_author } = stats;

  function fmtWords(w: number | null) {
    if (!w) return "—";
    if (w >= 1_000_000) return `${(w / 1_000_000).toFixed(1)}M`;
    if (w >= 1_000) return `${Math.round(w / 1_000)}K`;
    return `${w}`;
  }

  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-2">
        <StatCard label="Total books" value={`${summary.total_books}`} />
        <StatCard label="Avg WA" value={summary.avg_wa != null ? summary.avg_wa.toFixed(2) : "—"} />
        <StatCard label="Avg Total Avg" value={summary.avg_total_average != null ? summary.avg_total_average.toFixed(2) : "—"} />
        <StatCard label="Avg word count" value={fmtWords(summary.avg_words)} />
      </div>

      <SectionHeading>Per year</SectionHeading>
      <Table
        headers={["Year", "Books", "Avg WA", "Avg Total Avg", "Avg Words"]}
        rows={per_year.map((r) => [
          r.year, r.books,
          r.avg_wa?.toFixed(2) ?? null,
          r.avg_total_average?.toFixed(2) ?? null,
          fmtWords(r.avg_words),
        ])}
      />

      <SectionHeading>By genre</SectionHeading>
      <Table
        headers={["Genre", "Books", "Avg WA", "Avg Total Avg", "Avg Words"]}
        rows={by_genre.map((r) => [
          r.genre, r.books,
          r.avg_wa?.toFixed(2) ?? null,
          r.avg_total_average?.toFixed(2) ?? null,
          fmtWords(r.avg_words),
        ])}
      />

      <SectionHeading>By author</SectionHeading>
      <Table
        headers={["Author", "Books", "Avg WA"]}
        rows={by_author.map((r) => [r.author, r.books, r.avg_wa?.toFixed(2) ?? null])}
      />
    </div>
  );
}

/* ── Status tab ───────────────────────────────────────────────────────────── */

const LS_CR = "reading_ledger_currently_reading";
const LS_RN = "reading_ledger_reading_next";

function StatusTab({
  status,
  ratedTitles,
}: {
  status: ReadingStatusResponse;
  ratedTitles: string[];
}) {
  // Load status from localStorage (persists across page loads)
  const [currentlyReading, setCurrentlyReading] = useState<string[]>([]);
  const [readingNext, setReadingNext] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const poolSet = new Set(status.pool.map((p) => p.title));
    const cr = JSON.parse(localStorage.getItem(LS_CR) ?? "[]").filter((t: string) => poolSet.has(t));
    const rn = JSON.parse(localStorage.getItem(LS_RN) ?? "[]").filter((t: string) => poolSet.has(t));
    setCurrentlyReading(cr);
    setReadingNext(rn);
    setHydrated(true);
  }, [status.pool]);

  function toggleCR(title: string) {
    setCurrentlyReading((prev) => {
      const next = prev.includes(title) ? prev.filter((t) => t !== title) : [...prev, title];
      localStorage.setItem(LS_CR, JSON.stringify(next));
      return next;
    });
    // Clear from reading-next if moving to currently-reading
    setReadingNext((prev) => {
      if (!prev.includes(title)) return prev;
      const next = prev.filter((t) => t !== title);
      localStorage.setItem(LS_RN, JSON.stringify(next));
      return next;
    });
  }

  function toggleRN(title: string) {
    setReadingNext((prev) => {
      const next = prev.includes(title) ? prev.filter((t) => t !== title) : [...prev, title];
      localStorage.setItem(LS_RN, JSON.stringify(next));
      return next;
    });
    setCurrentlyReading((prev) => {
      if (!prev.includes(title)) return prev;
      const next = prev.filter((t) => t !== title);
      localStorage.setItem(LS_CR, JSON.stringify(next));
      return next;
    });
  }

  // Year-set form
  const [yearBook, setYearBook] = useState(ratedTitles[0] ?? "");
  const [yearVal, setYearVal] = useState(new Date().getFullYear());
  const [yearMsg, setYearMsg] = useState<string | null>(null);
  const [yearPending, startYearTransition] = useTransition();

  const poolTitles = status.pool.map((p) => p.title).sort();

  if (!hydrated) return null;

  function UnreadBlock({ title, books, emptyMsg }: { title: string; books: string[]; emptyMsg: string }) {
    return (
      <div>
        <h4 className="font-semibold text-sm mb-2" style={{ color: "var(--color-ink)" }}>{title}</h4>
        {books.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--color-faint)" }}>{emptyMsg}</p>
        ) : (
          <ul className="space-y-1">
            {books.map((t) => {
              const meta = status.pool.find((p) => p.title === t);
              return (
                <li key={t} className="text-sm">
                  <span className="font-medium" style={{ color: "var(--color-ink)" }}>{t}</span>
                  {(meta?.author || meta?.genre) ? (
                    <span style={{ color: "var(--color-muted)" }}>
                      {" "}· {[meta?.author, meta?.genre].filter(Boolean).join(" · ")}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div>
      {/* Current reading state */}
      <div className="grid sm:grid-cols-2 gap-6 mb-8">
        <div className="rounded-xl p-4 space-y-4" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
          <UnreadBlock title="📖 Currently reading" books={currentlyReading} emptyMsg="Nothing marked currently-reading." />
          <UnreadBlock title="🔜 Reading next" books={readingNext} emptyMsg="Nothing marked reading-next." />
        </div>

        <div className="rounded-xl p-4" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
          <h4 className="font-semibold text-sm mb-2" style={{ color: "var(--color-ink)" }}>
            ✓ Finished in {status.last_year ?? "—"}
          </h4>
          {status.finished.length === 0 ? (
            <p className="text-sm" style={{ color: "var(--color-faint)" }}>No finished books.</p>
          ) : (
            <ul className="space-y-1">
              {status.finished.slice(0, 12).map((b) => (
                <li key={b.title} className="text-sm">
                  <span className="font-medium" style={{ color: "var(--color-ink)" }}>{b.title}</span>
                  <span style={{ color: "var(--color-muted)" }}>
                    {" "}· {b.author} · {b.genre} · WA {b.wa.toFixed(2)} · rank {b.rank} of {b.total}
                  </span>
                </li>
              ))}
              {status.finished.length > 12 && (
                <li className="text-xs" style={{ color: "var(--color-faint)" }}>
                  …and {status.finished.length - 12} more
                </li>
              )}
            </ul>
          )}
        </div>
      </div>

      {/* Update pickers */}
      <div className="rounded-xl p-5 mb-8" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
        <h3 className="font-display font-semibold text-base mb-4" style={{ color: "var(--color-ink)" }}>
          Update what you&apos;re reading
        </h3>
        {poolTitles.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            No unread books yet — research books on the Predict page or add titles to your read queue.
          </p>
        ) : (
          <div className="space-y-2 max-h-80 overflow-y-auto">
            {poolTitles.map((title) => {
              const meta = status.pool.find((p) => p.title === title);
              const isCR = currentlyReading.includes(title);
              const isRN = readingNext.includes(title);
              return (
                <div
                  key={title}
                  className="flex items-center gap-3 px-3 py-2 rounded-lg"
                  style={{ background: "var(--color-surface-2)" }}
                >
                  <span className="flex-1 text-sm font-medium truncate" style={{ color: "var(--color-ink)" }}>
                    {title}
                    {meta?.genre ? <span className="ml-2 genre-chip">{meta.genre}</span> : null}
                  </span>
                  <button
                    onClick={() => toggleCR(title)}
                    className="text-xs px-2 py-1 rounded-md transition-colors flex-shrink-0"
                    style={{
                      background: isCR ? "var(--color-sage)" : "var(--color-surface)",
                      color: isCR ? "#fff" : "var(--color-muted)",
                      border: `1px solid ${isCR ? "var(--color-sage)" : "var(--color-rule)"}`,
                    }}
                  >
                    Reading
                  </button>
                  <button
                    onClick={() => toggleRN(title)}
                    className="text-xs px-2 py-1 rounded-md transition-colors flex-shrink-0"
                    style={{
                      background: isRN ? "var(--color-sage)" : "var(--color-surface)",
                      color: isRN ? "#fff" : "var(--color-muted)",
                      border: `1px solid ${isRN ? "var(--color-sage)" : "var(--color-rule)"}`,
                    }}
                  >
                    Next
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Year-read setter */}
      <div className="rounded-xl p-5" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
        <h3 className="font-display font-semibold text-base mb-4" style={{ color: "var(--color-ink)" }}>
          Set / edit year read
        </h3>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Book</label>
            <select
              value={yearBook}
              onChange={(e) => setYearBook(e.target.value)}
              className="rounded-lg px-3 py-2 text-sm"
              style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)", color: "var(--color-ink)" }}
            >
              {ratedTitles.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--color-muted)" }}>Year read</label>
            <input
              type="number"
              min={1900}
              max={2100}
              value={yearVal}
              onChange={(e) => setYearVal(parseInt(e.target.value))}
              className="rounded-lg px-3 py-2 text-sm w-28"
              style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)", color: "var(--color-ink)" }}
            />
          </div>
          <button
            disabled={yearPending}
            onClick={() => {
              startYearTransition(async () => {
                try {
                  await setYearRead(yearBook, yearVal);
                  setYearMsg("Year saved.");
                } catch (e) {
                  setYearMsg(e instanceof Error ? e.message : "Error saving year");
                }
              });
            }}
            className="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
            style={{
              background: yearPending ? "var(--color-sage-light)" : "var(--color-sage)",
              color: yearPending ? "var(--color-sage)" : "#fff",
            }}
          >
            {yearPending ? "Saving…" : "Save year"}
          </button>
        </div>
        {yearMsg && <p className="mt-3 text-sm" style={{ color: "var(--color-sage)" }}>{yearMsg}</p>}
      </div>
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────────────────────── */

export default function ReadingClient({
  stats,
  status,
  ratedTitles,
}: {
  stats: ReadingStatsResponse;
  status: ReadingStatusResponse;
  ratedTitles: string[];
}) {
  const [tab, setTab] = useState<Tab>("stats");

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Reading
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {stats.summary.total_books} books rated
        </p>
      </div>

      <SubTabs active={tab} onChange={setTab} />

      {tab === "stats" ? (
        <StatsTab stats={stats} />
      ) : (
        <StatusTab status={status} ratedTitles={ratedTitles} />
      )}
    </div>
  );
}
