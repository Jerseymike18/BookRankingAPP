"use client";

import Link from "next/link";
import { useEffect, useState, useRef } from "react";
import { lookupBook, addBook, addNonfictionBook, fetchRepredictRecent } from "@/lib/api";
import type { LookupResult, BookKind, RepredictReport, RepredictHandle } from "@/lib/types";
import { componentLabel } from "@/lib/format";
import { ProgressBar } from "@/components/ProgressBar";

import {
  clearAddBookDraft,
  isEmptyAddBookDraft,
  readAddBookDraft,
  scoredCount,
  writeAddBookDraft,
} from "@/lib/add-book-draft";

function fmtDelta(d: number): string {
  return `${d >= 0 ? "+" : ""}${d.toFixed(2)}`;
}

function deltaColor(d: number | null): string {
  if (d == null || Math.abs(d) < 0.005) return "var(--color-muted)";
  return d > 0 ? "var(--color-sage)" : "var(--color-spine-c)";
}

// Summary of the background cohort re-prediction a just-added book triggered:
// which unread books moved (same author, or same genre past the gate) and which
// were intentionally left alone. Reuses existing design tokens only.
function RepredictPanel({ report }: { report: RepredictReport }) {
  const t = report.trigger;
  const affected = report.affected ?? [];
  const suppressed = report.suppressed_genre_peers?.length ?? 0;
  const capped = report.capped_genre_peers?.length ?? 0;
  const nothing = affected.length === 0;
  return (
    <div
      className="rounded-lg px-4 py-3 mb-4"
      style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}
    >
      <div className="text-sm font-semibold mb-1" style={{ color: "var(--color-ink)" }}>
        Baseline re-prediction
      </div>
      <div className="text-xs mb-2" style={{ color: "var(--color-muted)" }}>
        {t.author_is_new ? `Establishing ${t.author} (first data point) ` : `${t.author ?? "This author"} `}
        {nothing
          ? "moved no unread books."
          : `re-predicted ${affected.length} unread book${affected.length === 1 ? "" : "s"}`}
        {!nothing && report.cohort_mean_d_wa != null ? ` · mean ΔWA ${fmtDelta(report.cohort_mean_d_wa)}` : ""}
      </div>
      {!nothing && (
        <ul className="space-y-1">
          {affected.map((m) => (
            <li key={m.title} className="flex items-center justify-between gap-3 text-xs">
              <span className="flex items-center gap-2 min-w-0">
                <span
                  className="shrink-0 rounded px-1.5 py-0.5"
                  style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", fontSize: "10px" }}
                >
                  {m.reason}
                </span>
                <span className="truncate" style={{ color: "var(--color-ink)" }}>
                  {m.title}
                </span>
              </span>
              <span className="shrink-0 tabular-nums" style={{ color: "var(--color-muted)" }}>
                {m.old_wa != null ? m.old_wa.toFixed(2) : "—"} → {m.new_wa.toFixed(2)}{" "}
                <span style={{ color: deltaColor(m.d_wa) }}>{m.d_wa != null ? fmtDelta(m.d_wa) : ""}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {(suppressed > 0 || capped > 0) && (
        <div className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
          {suppressed > 0 && `${suppressed} genre-peer${suppressed === 1 ? "" : "s"} left unchanged (gate). `}
          {capped > 0 && `${capped} deferred (cap).`}
        </div>
      )}
    </div>
  );
}

/* ── Shared input / label styles ────────────────────────────────────────── */

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-xs font-semibold uppercase tracking-widest mb-1"
      style={{ color: "var(--color-muted)" }}>
      {children}
    </label>
  );
}

function TextInput({
  value, onChange, placeholder, disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 disabled:opacity-50"
      style={inputStyle}
    />
  );
}

function NumberInput({
  value, onChange, min, max, step, disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
      className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 disabled:opacity-50"
      style={inputStyle}
    />
  );
}

/* ── Component score grid ── same visual as Rankings detail view ─────────── */

const COMPONENT_CATEGORIES_BY_KIND: Record<BookKind, Record<string, string[]>> = {
  fiction: {
    Story: ["Plot", "Entertainment", "Action", "Ending"],
    Character: ["Depth", "Emotional Impact", "Motivations"],
    Aesthetics: ["Prose", "Narration"],
    Theme: ["Insights", "Thought-Provokingness"],
    Worldbuilding: ["Depth2", "Integration", "Originality"],
  },
  nonfiction: {
    Substance: ["Informativeness", "Accuracy", "Originality"],
    Reasoning: ["Argumentation", "Evidence"],
    Exposition: ["Clarity", "Structure"],
    Aesthetics: ["Prose", "Voice"],
    Impact: ["Insights", "Thought-Provokingness", "Entertainment"],
  },
};

// Components a new book may leave blank — mirrors db_write._validate_scores /
// _validate_nonfiction_scores: worldbuilding is optional for realist fiction
// genres; nonfiction has no optional components.
const OPTIONAL_COMPONENTS_BY_KIND: Record<BookKind, Set<string>> = {
  fiction: new Set(["Depth2", "Integration", "Originality"]),
  nonfiction: new Set(),
};

function defaultScores(kind: BookKind): Record<string, string> {
  return Object.fromEntries(
    Object.values(COMPONENT_CATEGORIES_BY_KIND[kind]).flat().map((c) => [c, ""])
  );
}

const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

/* ── Score input helpers ── raw string state so a box can go empty without
   snapping back to 0; empty is validated (required-vs-optional) at submit. ── */

const SCORE_INPUT_RE = /^-?\d*\.?\d*$/;

function clampScoreInput(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === "") return raw;
  const v = parseFloat(trimmed);
  if (isNaN(v)) return raw;
  const clamped = Math.min(10, Math.max(0, v));
  return clamped === v ? raw : String(clamped);
}

/** Parses only the boxes with a real, parseable value — empty/unparseable
 * boxes are simply absent from the result (caller checks required fields). */
function parseScores(raw: Record<string, string>): Record<string, number> {
  const parsed: Record<string, number> = {};
  for (const [comp, str] of Object.entries(raw)) {
    const trimmed = str.trim();
    if (trimmed === "") continue;
    const v = parseFloat(trimmed);
    if (isNaN(v)) continue;
    parsed[comp] = Math.min(10, Math.max(0, v));
  }
  return parsed;
}

function ScoreGrid({
  categories,
  scores,
  onChange,
  kind,
}: {
  categories: Record<string, string[]>;
  scores: Record<string, string>;
  onChange: (comp: string, val: string) => void;
  kind: BookKind;
}) {
  return (
    <div className="space-y-5">
      {Object.entries(categories).map(([cat, comps]) => (
        <div key={cat}>
          <p className="text-xs font-semibold uppercase tracking-widest mb-2"
            style={{ color: "var(--color-muted)" }}>
            {cat}
          </p>
          <div
            className="grid gap-3"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))" }}
          >
            {comps.map((comp) => (
              <div key={comp}>
                <label className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>
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
      ))}
    </div>
  );
}

/* ── Main component ─────────────────────────────────────────────────────── */

export default function AddBookClient({
  validGenres,
}: {
  categoryOrder: string[];
  validGenres: string[];
}) {
  // Fiction vs nonfiction — drives the component set, the genre field, and the
  // target table.
  const [kind, setKind] = useState<BookKind>("fiction");
  const categories = COMPONENT_CATEGORIES_BY_KIND[kind];

  // Lookup state
  const [lookupTitle, setLookupTitle] = useState("");
  const [lookupAuthorHint, setLookupAuthorHint] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupResult, setLookupResult] = useState<LookupResult | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);

  // Form state
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [genre, setGenre] = useState(validGenres[0] ?? "");
  const [series, setSeries] = useState("");
  const [seriesNumber, setSeriesNumber] = useState<number | null>(null);
  const [words, setWords] = useState(0);
  const [yearRead, setYearRead] = useState(new Date().getFullYear());
  const [monthRead, setMonthRead] = useState(new Date().getMonth() + 1);
  const [scores, setScores] = useState<Record<string, string>>(defaultScores("fiction"));
  const [prefilled, setPrefilled] = useState(false);
  // Set when the form came back from a stored draft, so the reader is told these
  // are their own earlier answers rather than finding the boxes mysteriously full.
  const [restored, setRestored] = useState<number | null>(null);

  function changeKind(k: BookKind) {
    setKind(k);
    setScores(defaultScores(k));
    setSaveError(null);
    setSaveSuccess(null);
  }

  /* ── Keep a half-rated book across navigations ────────────────────────────
     Rating a book is the heaviest entry in the app — 14 component boxes plus the
     metadata — and all of it used to die on any unmount: a nav click, a reload, a
     back gesture, or an expired session bouncing the reader to /login mid-entry.
     There is no partial save to fall back on (`add_book` refuses a book missing
     any required component), so the draft IS the protection.

     Declared before the hydrate effect below, and that order is load-bearing:
     effects run in declaration order, so on the pass where hydrate restores, this
     one has already run and skipped. Declared after, it would run in that same
     pass still holding the empty form and write a blank draft straight over the
     one hydrate had just read. */
  const hydrated = useRef(false);
  useEffect(() => {
    if (!hydrated.current) return;
    const draft = {
      kind, title, author, genre, series, seriesNumber,
      words, yearRead, monthRead, scores, prefilled,
    };
    // An untouched form has nothing to protect, and storing a blank would let a
    // later visit "restore" emptiness over nothing.
    if (isEmptyAddBookDraft(draft)) {
      clearAddBookDraft();
      return;
    }
    writeAddBookDraft(draft);
  }, [kind, title, author, genre, series, seriesNumber, words, yearRead, monthRead,
      scores, prefilled]);

  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const d = readAddBookDraft(
      (k) => Object.values(COMPONENT_CATEGORIES_BY_KIND[k]).flat(),
      validGenres
    );
    /* eslint-disable react-hooks/set-state-in-effect --
       Restoring editable state from an external store (sessionStorage) once on
       mount is the case the rule can't tell apart from deriving state from props,
       and it is the one React allows. It can't move to a lazy useState
       initializer: the page is server-rendered, so reading storage during the
       first client render would make that render disagree with the server's
       markup. These run once, behind the ref guard, in one batch. */
    if (d && !isEmptyAddBookDraft(d)) {
      setKind(d.kind);
      setTitle(d.title);
      setAuthor(d.author);
      // The codec has already dropped a genre the reader no longer has; fall back
      // to the same default a fresh form uses rather than an empty select.
      setGenre(d.genre || validGenres[0] || "");
      setSeries(d.series);
      setSeriesNumber(d.seriesNumber);
      setWords(d.words);
      setYearRead(d.yearRead);
      setMonthRead(d.monthRead);
      // Merged onto a full set of boxes for the restored kind, so a component
      // added since the draft was written still gets an (empty) input.
      setScores({ ...defaultScores(d.kind), ...d.scores });
      setPrefilled(d.prefilled);
      setRestored(scoredCount(d));
    }
    /* eslint-enable react-hooks/set-state-in-effect */
    // Mount-only: the ref guard makes it run once, and `validGenres` is a server
    // prop that doesn't change for this page's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Save state
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

  // Background cohort re-prediction (fiction only): the add returns instantly and
  // we poll for the report. pollIdRef supersedes an in-flight poll if the user
  // adds another book before the previous cohort pass reports back.
  const [repredictStatus, setRepredictStatus] = useState<"idle" | "running" | "done">("idle");
  const [repredictReport, setRepredictReport] = useState<RepredictReport | null>(null);
  const pollIdRef = useRef(0);

  async function pollRepredict(token: string) {
    const myId = ++pollIdRef.current; // supersede any earlier poll
    setRepredictReport(null);
    setRepredictStatus("running");
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    for (let i = 0; i < 60; i++) {
      if (pollIdRef.current !== myId) return; // a newer add took over
      try {
        const poll = await fetchRepredictRecent(token);
        if (poll.status === "done") {
          if (pollIdRef.current !== myId) return;
          setRepredictReport(poll.report);
          setRepredictStatus(poll.report ? "done" : "idle");
          return;
        }
      } catch {
        // transient network hiccup — keep polling
      }
      await sleep(1500);
    }
    if (pollIdRef.current === myId) setRepredictStatus("idle"); // timed out; hide
  }

  function handleScoreChange(comp: string, val: string) {
    setScores((prev) => ({ ...prev, [comp]: val }));
  }

  async function handleLookup() {
    if (!lookupTitle.trim()) {
      setLookupError("Enter a title first.");
      return;
    }
    setLookupLoading(true);
    setLookupError(null);
    setLookupResult(null);
    try {
      const result = await lookupBook(lookupTitle.trim(), lookupAuthorHint.trim() || undefined);
      setLookupResult(result);
    } catch (e: unknown) {
      setLookupError(e instanceof Error ? e.message : "Look-up failed.");
    } finally {
      setLookupLoading(false);
    }
  }

  function applyLookup() {
    if (!lookupResult) return;
    setTitle(lookupResult.title);
    setAuthor(lookupResult.author);
    if (lookupResult.genre && validGenres.includes(lookupResult.genre)) {
      setGenre(lookupResult.genre);
    }
    setWords(lookupResult.words ?? 0);
    setSeries(lookupResult.series ?? "");
    setSeriesNumber(lookupResult.series_number ?? null);
    setPrefilled(true);
    setLookupResult(null);
    setLookupTitle("");
    setLookupAuthorHint("");
  }

  function clearLookup() {
    setLookupResult(null);
    setLookupError(null);
  }

  async function handleSubmit() {
    // A new book must have every required rating (worldbuilding is optional
    // for fiction; nonfiction has no optional components). Empty boxes are
    // never silently saved as 0 — block and name what's missing.
    const parsedScores = parseScores(scores);
    const required = Object.values(categories).flat()
      .filter((c) => !OPTIONAL_COMPONENTS_BY_KIND[kind].has(c));
    const missing = required.filter((c) => parsedScores[c] === undefined);
    if (missing.length > 0) {
      setSaveError(`Missing required score(s): ${missing.join(", ")}.`);
      return;
    }

    setSaving(true);
    setSaveError(null);
    setSaveSuccess(null);
    try {
      const common = {
        title,
        author,
        scores: parsedScores,
        series: series.trim() || undefined,
        series_number: seriesNumber ?? undefined,
        words: words > 0 ? words : undefined,
        year_read: yearRead,
        read_month: monthRead,
      };
      const submittedTitle = title;
      let handle: RepredictHandle | null = null;
      if (kind === "nonfiction") {
        const result = await addNonfictionBook(common);
        setSaveSuccess(result.message || `Added "${submittedTitle}" to the ledger.`);
      } else {
        const result = await addBook({ ...common, genre });
        setSaveSuccess(result.message || `Added "${submittedTitle}" to the ledger.`);
        handle = result.repredict ?? null;
      }

      // Fiction adds fire a background cohort re-prediction; poll for its report.
      if (handle && handle.status === "running") {
        void pollRepredict(handle.token);
      } else {
        pollIdRef.current += 1; // cancel any in-flight poll
        setRepredictStatus("idle");
        setRepredictReport(null);
      }

      // Reset form. The book is in the library now, so the draft has nothing left
      // to protect — and leaving it would restore the book they just added.
      clearAddBookDraft();
      setRestored(null);
      setTitle("");
      setAuthor("");
      setGenre(validGenres[0] ?? "");
      setSeries("");
      // Was omitted here, so a volume number picked up by a metadata lookup
      // survived the reset and rode along on the NEXT book — which has no series
      // number input of its own, so nothing on screen showed it. `add_book` does
      // not require a series alongside it, so that book stored a series_number
      // with a null series.
      setSeriesNumber(null);
      setWords(0);
      setYearRead(new Date().getFullYear());
      setMonthRead(new Date().getMonth() + 1);
      setScores(defaultScores(kind));
      setPrefilled(false);
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Could not add book.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}>
          Add a Book
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {kind === "nonfiction"
            ? "Scores are 0–10 across Substance / Reasoning / Exposition / Aesthetics / Impact (12 components)."
            : "Scores are 0–10. Worldbuilding components (Depth2 / Integration / Originality) may be left blank for realist genres."}
        </p>
      </div>

      {/* Says so when the form came back from a draft, rather than letting the
          reader wonder why the boxes are full. Dismissible: it has done its job
          once it has been read. */}
      {restored !== null && (
        <div className="flex items-start justify-between gap-3 mb-6">
          <p className="text-sm" style={{ color: "var(--color-sage)" }}>
            Picked up where you left off — this book was still half-entered
            {restored > 0 ? ` (${restored} score${restored === 1 ? "" : "s"} filled in)` : ""}.
          </p>
          <button
            onClick={() => setRestored(null)}
            className="text-xs font-medium shrink-0"
            style={{ color: "var(--color-muted)" }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Onboarding shortcut — bulk-import instead of adding one at a time. */}
      <Link
        href="/import"
        className="block rounded-lg px-4 py-3 mb-6 text-sm no-underline transition-colors"
        style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" }}
      >
        New here?{" "}
        <span className="font-semibold">Import your Goodreads library</span>{" "}
        to rank many books at once instead of adding them one by one →
      </Link>

      {/* Fiction / Nonfiction toggle — drives the component set + target table */}
      <div className="flex gap-1 mb-8 p-1 rounded-xl inline-flex" style={{ background: "var(--color-surface-2)" }}>
        {(["fiction", "nonfiction"] as BookKind[]).map((k) => (
          <button
            key={k}
            onClick={() => changeKind(k)}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors capitalize"
            style={{
              background: kind === k ? "var(--color-surface)" : "transparent",
              color: kind === k ? "var(--color-sage)" : "var(--color-muted)",
              boxShadow: kind === k ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
            }}
          >
            {k}
          </button>
        ))}
      </div>

      {/* ── Lookup panel ───────────────────────────────────────────────────── */}
      <section
        className="rounded-xl p-5 mb-8"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <h2 className="font-display font-semibold text-base mb-1" style={{ color: "var(--color-ink)" }}>
          Look up book metadata
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          Type a title and click Look up — the LLM will find the author, genre, word count, and series so you don&apos;t have to. Books you&apos;ve already predicted are filled straight from that prediction, no LLM call.
        </p>

        <div className="flex flex-wrap gap-3 mb-3">
          <div className="flex-1 min-w-48">
            <FieldLabel>Title to look up</FieldLabel>
            <TextInput
              value={lookupTitle}
              onChange={setLookupTitle}
              placeholder="e.g. The Name of the Wind"
              disabled={lookupLoading}
            />
          </div>
          <div className="flex-1 min-w-40">
            <FieldLabel>Author hint (optional)</FieldLabel>
            <TextInput
              value={lookupAuthorHint}
              onChange={setLookupAuthorHint}
              placeholder="e.g. Rothfuss"
              disabled={lookupLoading}
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleLookup}
              disabled={lookupLoading}
              className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors"
              style={{
                background: "var(--color-sage)",
                color: "#fff",
              }}
            >
              {lookupLoading ? "Looking up…" : "Look up"}
            </button>
          </div>
        </div>

        {lookupLoading && (
          <ProgressBar
            className="mt-2"
            label="Looking up this book's metadata…"
          />
        )}

        {lookupError && (
          <div className="rounded-lg px-4 py-3 text-sm mt-2"
            style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}>
            {lookupError}
          </div>
        )}

        {lookupResult && (
          <div className="rounded-lg px-4 py-4 mt-3"
            style={{ background: "var(--color-sage-light)", border: "1px solid var(--color-sage)" }}>
            {lookupResult.source === "prediction" && (
              <p className="text-xs font-semibold mb-2" style={{ color: "var(--color-sage)" }}>
                ★ From your existing prediction — no LLM call
              </p>
            )}
            <p className="text-sm font-semibold mb-0.5" style={{ color: "var(--color-ink)" }}>
              Found: <span className="font-bold">{lookupResult.title}</span> by {lookupResult.author}
            </p>
            <p className="text-xs mb-2" style={{ color: "var(--color-muted)" }}>
              {lookupResult.genre ?? "(genre unknown)"} ·{" "}
              {lookupResult.words ? `~${lookupResult.words.toLocaleString()} words` : "word count unknown"} ·{" "}
              {lookupResult.series || "standalone"}
            </p>
            {lookupResult.blurb && (
              <p className="text-xs mb-3 italic" style={{ color: "var(--color-muted)" }}>
                {lookupResult.blurb}
              </p>
            )}
            <div className="flex gap-2">
              <button
                onClick={applyLookup}
                className="px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                ✓ Use this — fill the form
              </button>
              <button
                onClick={clearLookup}
                className="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                style={{
                  background: "var(--color-surface)",
                  color: "var(--color-muted)",
                  border: "1px solid var(--color-rule)",
                }}
              >
                ✗ Wrong book — clear
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ── Book form ──────────────────────────────────────────────────────── */}
      <section
        className="rounded-xl p-5 mb-6"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        {prefilled && (
          <p className="text-xs mb-4 px-3 py-2 rounded-lg"
            style={{ background: "var(--color-sage-light)", color: "var(--color-sage)" }}>
            Metadata pre-filled from look-up — all fields are editable.
          </p>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          <div>
            <FieldLabel>Title</FieldLabel>
            <TextInput value={title} onChange={setTitle} placeholder="Book title" />
          </div>
          <div>
            <FieldLabel>Author</FieldLabel>
            <TextInput value={author} onChange={setAuthor} placeholder="Author name" />
          </div>
          {kind === "fiction" && (
            <div>
              <FieldLabel>Genre</FieldLabel>
              <select
                value={genre}
                onChange={(e) => setGenre(e.target.value)}
                className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
                style={inputStyle}
              >
                {validGenres.map((g) => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>
          )}
          <div>
            <FieldLabel>Series (optional)</FieldLabel>
            <TextInput value={series} onChange={setSeries} placeholder="e.g. The Kingkiller Chronicle #1" />
          </div>
          <div>
            <FieldLabel>Word count (estimate)</FieldLabel>
            <NumberInput value={words} onChange={setWords} min={0} step={1000} />
          </div>
          <div>
            <FieldLabel>Year read</FieldLabel>
            <NumberInput value={yearRead} onChange={(v) => setYearRead(Math.round(v))} min={1900} max={2100} step={1} />
          </div>
          <div>
            <FieldLabel>Month read</FieldLabel>
            <select
              value={monthRead}
              onChange={(e) => setMonthRead(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
              style={inputStyle}
            >
              {MONTHS.map((m, i) => (
                <option key={m} value={i + 1}>{m}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Component scores */}
        <div
          className="pt-5"
          style={{ borderTop: "1px solid var(--color-rule)" }}
        >
          <h3 className="font-display font-semibold text-sm mb-4" style={{ color: "var(--color-ink)" }}>
            Component scores
          </h3>
          <ScoreGrid categories={categories} scores={scores} onChange={handleScoreChange} kind={kind} />
        </div>
      </section>

      {/* Save feedback */}
      {saveError && (
        <div className="rounded-lg px-4 py-3 text-sm mb-4"
          style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}>
          {saveError}
        </div>
      )}
      {saveSuccess && (
        <div className="rounded-lg px-4 py-3 text-sm mb-4"
          style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" }}>
          {saveSuccess}
        </div>
      )}
      {repredictStatus === "running" && (
        <div className="rounded-lg px-4 py-3 mb-4"
          style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}>
          {/* The backend reports only pending/done for this pass — no partial
              count exists to drive a percentage, so the bar is indeterminate. */}
          <ProgressBar
            label="Re-predicting related unread books…"
            hint="Your book is already saved — this runs in the background and reports below."
          />
        </div>
      )}
      {repredictStatus === "done" && repredictReport && (
        <RepredictPanel report={repredictReport} />
      )}

      <button
        onClick={handleSubmit}
        disabled={saving || !title.trim() || !author.trim()}
        className="px-6 py-3 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
        style={{ background: "var(--color-sage)", color: "#fff" }}
      >
        {saving ? "Adding…" : "Add book to ledger"}
      </button>
    </div>
  );
}
