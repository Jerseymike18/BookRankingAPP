"use client";

import { useState } from "react";
import { lookupBook, addBook, addNonfictionBook } from "@/lib/api";
import type { LookupResult, BookKind } from "@/lib/types";

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
    Quality: ["Informativeness", "Argumentation", "Entertainment"],
    Aesthetics: ["Prose", "Phraseology"],
    Theme: ["Insights", "Philosophizing", "Thought-Provokingness"],
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
}: {
  categories: Record<string, string[]>;
  scores: Record<string, string>;
  onChange: (comp: string, val: string) => void;
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
  const [scores, setScores] = useState<Record<string, string>>(defaultScores("fiction"));
  const [prefilled, setPrefilled] = useState(false);

  function changeKind(k: BookKind) {
    setKind(k);
    setScores(defaultScores(k));
    setSaveError(null);
    setSaveSuccess(null);
  }

  // Save state
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

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
      };
      const result =
        kind === "nonfiction"
          ? await addNonfictionBook(common)
          : await addBook({ ...common, genre });
      setSaveSuccess(result.message || `Added "${title}" to the ledger.`);
      // Reset form
      setTitle("");
      setAuthor("");
      setGenre(validGenres[0] ?? "");
      setSeries("");
      setWords(0);
      setYearRead(new Date().getFullYear());
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
            ? "Scores are 0–10 across Quality / Aesthetics / Theme (8 components)."
            : "Scores are 0–10. Worldbuilding components (Depth2 / Integration / Originality) may be left blank for realist genres."}
        </p>
      </div>

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
          Type a title and click Look up — the LLM will find the author, genre, word count, and series so you don't have to.
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

        {lookupError && (
          <div className="rounded-lg px-4 py-3 text-sm mt-2"
            style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}>
            {lookupError}
          </div>
        )}

        {lookupResult && (
          <div className="rounded-lg px-4 py-4 mt-3"
            style={{ background: "var(--color-sage-light)", border: "1px solid var(--color-sage)" }}>
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
        </div>

        {/* Component scores */}
        <div
          className="pt-5"
          style={{ borderTop: "1px solid var(--color-rule)" }}
        >
          <h3 className="font-display font-semibold text-sm mb-4" style={{ color: "var(--color-ink)" }}>
            Component scores
          </h3>
          <ScoreGrid categories={categories} scores={scores} onChange={handleScoreChange} />
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
