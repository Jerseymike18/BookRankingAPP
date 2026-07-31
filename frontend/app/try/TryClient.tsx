"use client";

import { useState } from "react";
import { demoPredict } from "@/lib/api";
import type { DemoPrediction, ResearchResult } from "@/lib/types";

// The static public showcase has no backend to answer /api/demo/predict, so the
// live demo only runs on the hosted app. If someone lands here on the static build,
// point them at the app rather than let the fetch fail.
const STATIC = process.env.NEXT_PUBLIC_STATIC_DATA === "1";

/* Curated example books — each verified to already be in the engine's analyzed
   set, spanning genres, so a click predicts instantly and for free. */
const EXAMPLES: { title: string; author: string }[] = [
  { title: "A Game of Thrones", author: "George R. R. Martin" },
  { title: "Hyperion", author: "Dan Simmons" },
  { title: "Dune", author: "Frank Herbert" },
  { title: "The Name of the Wind", author: "Patrick Rothfuss" },
  { title: "Project Hail Mary", author: "Andy Weir" },
  { title: "Blood Meridian", author: "Cormac McCarthy" },
  { title: "The Fifth Season", author: "N. K. Jemisin" },
  { title: "The Lies of Locke Lamora", author: "Scott Lynch" },
];

/* ── Grounding badge — the PRIMARY reliability signal (ported from Predict; reuses
   the existing design tokens, no new styles). ─────────────────────────────────── */
function GroundingBadge({ nGenre, nAuthor }: { nGenre: number; nAuthor: number }) {
  let level: "strong" | "moderate" | "thin" | "very-thin";
  let label: string;
  let detail: string;

  if (nGenre === 0) {
    level = "very-thin";
    label = "Very thin grounding";
    detail = `No rated books in this genre (${nAuthor} by this author). Treat as a rough guess.`;
  } else if (nGenre <= 3 && nAuthor === 0) {
    level = "thin";
    label = "Thin grounding";
    detail = `Only ${nGenre} rated book(s) in this genre, 0 by this author. Lean on this less.`;
  } else if (nGenre >= 5 || nAuthor >= 1) {
    level = "strong";
    const authorNote = nAuthor >= 1 ? `, ${nAuthor} by this author` : ", 0 by this author";
    label = "Strong grounding";
    detail = `Based on ${nGenre} rated book(s) in this genre${authorNote}.`;
  } else {
    level = "moderate";
    label = "Moderate grounding";
    detail = `Based on ${nGenre} rated book(s) in this genre, ${nAuthor} by this author.`;
  }

  const colors: Record<typeof level, { bg: string; border: string; text: string }> = {
    strong: { bg: "var(--color-sage-light)", border: "var(--color-sage)", text: "var(--color-sage)" },
    moderate: { bg: "#EFF6FF", border: "#93C5FD", text: "#1D4ED8" },
    thin: { bg: "#FFFBEB", border: "#FCD34D", text: "#92400E" },
    "very-thin": { bg: "#FEF2F2", border: "#FCA5A5", text: "#B91C1C" },
  };
  const c = colors[level];

  return (
    <div className="rounded-lg px-4 py-3 text-sm" style={{ background: c.bg, border: `1px solid ${c.border}` }}>
      <p className="font-semibold mb-0.5" style={{ color: c.text }}>{label}</p>
      <p style={{ color: c.text }}>{detail}</p>
    </div>
  );
}

/* ── Component grid (read-only, mirrors Rankings / Predict) ───────────────────── */
function ComponentGrid({
  components,
  categoryOrder,
}: {
  components: ResearchResult["components"];
  categoryOrder: string[];
}) {
  return (
    <div className="space-y-4">
      {categoryOrder.map((cat) => {
        const comps = components[cat];
        if (!comps) return null;
        return (
          <div key={cat}>
            <p className="text-xs font-semibold uppercase tracking-widest mb-2" style={{ color: "var(--color-muted)" }}>
              {cat}
            </p>
            <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(5rem, 1fr))" }}>
              {Object.entries(comps).map(([comp, val]) => (
                <div key={comp} className="comp-tile">
                  <span className="comp-label">{comp}</span>
                  <span className="comp-value">{val !== null ? val.toFixed(2) : "—"}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── The prediction result (always expanded, read-only) ──────────────────────── */
function ResultCard({ result }: { result: ResearchResult }) {
  return (
    <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--color-rule)" }}>
      <div className="flex items-center gap-4 px-5 py-4" style={{ background: "var(--color-surface)" }}>
        <div className="wa-badge flex-shrink-0" style={{ width: "3rem", height: "3rem", fontSize: "0.9rem" }}>
          {result.wa.toFixed(2)}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-display font-semibold text-lg leading-tight" style={{ color: "var(--color-ink)" }}>
            {result.title}
          </p>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>{result.author}</p>
        </div>
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          <span className="genre-chip">{result.genre}</span>
          <span className="text-xs" style={{ color: "var(--color-faint)" }}>
            would rank ~#{result.rank} of {result.total}
          </span>
        </div>
      </div>

      <div
        className="px-5 py-4 space-y-4"
        style={{ borderTop: "1px solid var(--color-rule)", background: "var(--color-ground)" }}
      >
        {result.words ? (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            ~{result.words.toLocaleString()} words
          </p>
        ) : null}

        {/* PRIMARY reliability signal */}
        <GroundingBadge nGenre={result.n_genre} nAuthor={result.n_author} />

        {/* Empirical 80% interval from leave-one-out residuals at this data density */}
        {result.wa_low != null && result.wa_high != null && (
          <p className="text-sm" style={{ color: "var(--color-ink)" }}>
            <strong>{result.wa.toFixed(1)}</strong>{" "}
            <span style={{ color: "var(--color-muted)" }}>
              ({result.wa_low.toFixed(1)}–{result.wa_high.toFixed(1)}, 80% interval)
            </span>
            {result.bucket_label && (
              <span style={{ color: "var(--color-faint)" }}>
                {" · "}
                {result.bucket_label}
                {result.pooled && " (pooled)"}
              </span>
            )}
          </p>
        )}

        {result.blurb && (
          <p className="text-sm leading-relaxed" style={{ color: "var(--color-muted)" }}>
            {result.blurb}
          </p>
        )}

        <ComponentGrid components={result.components} categoryOrder={result.category_order} />

        <p className="text-xs" style={{ color: "var(--color-faint)" }}>
          {result.from_cache
            ? "Scored from the engine's stored analysis of this book, then ranked live against the library."
            : "Freshly researched and predicted just now."}
        </p>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

export default function TryClient() {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DemoPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(t: string, a: string) {
    const tt = t.trim();
    if (!tt || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await demoPredict(tt, a.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong — try again.");
    } finally {
      setLoading(false);
    }
  }

  function pick(t: string, a: string) {
    setTitle(t);
    setAuthor(a);
    run(t, a);
  }

  if (STATIC) {
    return (
      <div className="max-w-xl mx-auto text-center space-y-4 py-12">
        <h1 className="font-display text-2xl font-semibold" style={{ color: "var(--color-ink)" }}>
          Try the prediction engine
        </h1>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          The live demo runs on the app.{" "}
          <a href="https://www.thereadingledger.com/try" className="underline" style={{ color: "var(--color-sage)" }}>
            Open it at thereadingledger.com/try →
          </a>
        </p>
      </div>
    );
  }

  const ready = result && result.available;

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      {/* Hero */}
      <header className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-sage)" }}>
          Live demo · no sign-up
        </p>
        <h1 className="font-display text-3xl font-semibold leading-tight" style={{ color: "var(--color-ink)" }}>
          Predict a book before you read it
        </h1>
        <p className="text-sm leading-relaxed" style={{ color: "var(--color-muted)" }}>
          The Reading Ledger rates every book across 14 fine-grained components and learns one
          reader&rsquo;s taste. Type any book below to see its predicted enjoyment score, where it
          would rank in the library, and a calibrated 80% confidence range — computed live by the
          real engine.
        </p>
      </header>

      {/* Input */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(title, author);
        }}
        className="space-y-3"
      >
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Book title"
            aria-label="Book title"
            className="flex-1 px-3 py-2 rounded-lg text-sm focus:outline-none focus:ring-2"
            style={inputStyle}
          />
          <input
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            placeholder="Author (optional)"
            aria-label="Author (optional)"
            className="flex-1 px-3 py-2 rounded-lg text-sm focus:outline-none focus:ring-2"
            style={inputStyle}
          />
          <button
            type="submit"
            disabled={loading || !title.trim()}
            className="px-5 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
            style={{ background: "var(--color-sage)", color: "#fff" }}
          >
            {loading ? "Predicting…" : "Predict"}
          </button>
        </div>
      </form>

      {/* Example chips */}
      <div className="space-y-2">
        <p className="text-xs" style={{ color: "var(--color-faint)" }}>
          Or try one of these:
        </p>
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((b) => (
            <button
              key={b.title}
              onClick={() => pick(b.title, b.author)}
              disabled={loading}
              className="px-3 py-1.5 rounded-full text-xs font-medium disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-surface-2)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}
            >
              {b.title}
            </button>
          ))}
        </div>
      </div>

      {/* Result / states */}
      <div className="min-h-[2rem]">
        {loading && (
          <p className="text-sm animate-pulse" style={{ color: "var(--color-muted)" }}>
            Analyzing {title.trim() || "book"} against the library…
          </p>
        )}

        {!loading && error && (
          <div className="rounded-lg px-4 py-3 text-sm" style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}>
            {error}
          </div>
        )}

        {!loading && result && !result.available && (
          <div
            className="rounded-lg px-4 py-3 text-sm"
            style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" }}
          >
            {result.message}
          </div>
        )}

        {!loading && ready && <ResultCard result={result as ResearchResult} />}
      </div>

      {/* Footer CTA */}
      <footer className="pt-4 text-xs leading-relaxed" style={{ color: "var(--color-faint)", borderTop: "1px solid var(--color-rule)" }}>
        <p className="pt-4">
          This is a live, read-only demo of a personal book-tracking and prediction app.{" "}
          <a href="/login" className="underline" style={{ color: "var(--color-sage)" }}>
            Create your own ledger →
          </a>
        </p>
      </footer>
    </div>
  );
}
