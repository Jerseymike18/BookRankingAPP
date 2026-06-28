"use client";

import { useState, useEffect, useRef } from "react";
import {
  predictInstant,
  predictResearch,
  discoverCandidates,
  saveRecommendation,
} from "@/lib/api";
import type {
  Book,
  InstantPrediction,
  ResearchResult,
  Candidate,
  ScoredCandidate,
} from "@/lib/types";
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";

/* ── Candidate table columns ─────────────────────────────────────────────── */

const CANDIDATE_COLS: ColDef<Candidate>[] = [
  { key: "title",  label: "Title",  type: "string", getValue: (c) => c.title },
  { key: "author", label: "Author", type: "string", getValue: (c) => c.author },
  { key: "genre",  label: "Genre",  type: "string", getValue: (c) => c.genre ?? "",
    formatter: (v) => v ? <span className="genre-chip">{v}</span> : <span style={{ color: "var(--color-faint)", fontSize: "0.75rem" }}>auto-detect</span> },
  { key: "series", label: "Series", type: "string", getValue: (c) => c.series ?? "",
    formatter: (v) => v ? <>{v}</> : <span style={{ color: "var(--color-faint)" }}>—</span> },
  { key: "series_number", label: "#", type: "numeric", getValue: (c) => c.series_number ?? null,
    formatter: (v) => (v === null || v === undefined) ? <span style={{ color: "var(--color-faint)" }}>—</span> : <>{v}</> },
  { key: "status", label: "Status", type: "string", getValue: (c) => (c as Candidate).cached ? "cached" : "new",
    sortable: false },
];

/* ── Shared input styles ─────────────────────────────────────────────────── */

const inputCls =
  "w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2";
const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label
      className="block text-xs font-semibold uppercase tracking-widest mb-1"
      style={{ color: "var(--color-muted)" }}
    >
      {children}
    </label>
  );
}

/* ── Grounding signal (the PRIMARY reliability indicator) ────────────────── */

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
    strong:    { bg: "var(--color-sage-light)", border: "var(--color-sage)", text: "var(--color-sage)" },
    moderate:  { bg: "#EFF6FF", border: "#93C5FD", text: "#1D4ED8" },
    thin:      { bg: "#FFFBEB", border: "#FCD34D", text: "#92400E" },
    "very-thin": { bg: "#FEF2F2", border: "#FCA5A5", text: "#B91C1C" },
  };
  const c = colors[level];

  return (
    <div
      className="rounded-lg px-4 py-3 text-sm"
      style={{ background: c.bg, border: `1px solid ${c.border}` }}
    >
      <p className="font-semibold mb-0.5" style={{ color: c.text }}>
        {label}
      </p>
      <p style={{ color: c.text }}>{detail}</p>
    </div>
  );
}

/* ── Component grid (read-only, mirrors Rankings) ────────────────────────── */

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
            <p
              className="text-xs font-semibold uppercase tracking-widest mb-2"
              style={{ color: "var(--color-muted)" }}
            >
              {cat}
            </p>
            <div
              className="grid gap-1.5"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(5rem, 1fr))" }}
            >
              {Object.entries(comps).map(([comp, val]) => (
                <div key={comp} className="comp-tile">
                  <span className="comp-label">{comp}</span>
                  <span className="comp-value">
                    {val !== null ? val.toFixed(2) : "—"}
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

/* ── WA result card ──────────────────────────────────────────────────────── */

function WACard({
  wa,
  ci,
  rank,
  total,
  label,
  rankRange,
}: {
  wa: number;
  ci: [number, number];
  rank: number;
  total: number;
  label?: string;
  rankRange?: [number, number];
}) {
  return (
    <div className="flex items-start gap-6">
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div
          className="w-20 h-20 rounded-full flex items-center justify-center font-display font-bold text-2xl shadow-sm"
          style={{ background: "var(--color-sage)", color: "#fff" }}
        >
          {wa.toFixed(2)}
        </div>
        {label && (
          <span className="text-xs text-center" style={{ color: "var(--color-muted)" }}>
            {label}
          </span>
        )}
      </div>
      <div className="pt-1 space-y-1">
        <p className="text-sm" style={{ color: "var(--color-ink)" }}>
          <span className="font-semibold">90% interval:</span>{" "}
          {ci[0].toFixed(2)} – {ci[1].toFixed(2)}
        </p>
        <p className="text-sm" style={{ color: "var(--color-ink)" }}>
          <span className="font-semibold">Predicted rank:</span> ~{rank} of {total}
          {rankRange && (
            <span style={{ color: "var(--color-muted)" }}>
              {" "}(range {rankRange[0]}–{rankRange[1]})
            </span>
          )}
        </p>
      </div>
    </div>
  );
}

/* ── Instant prediction decomposition chain ──────────────────────────────── */

function InstantDecomposition({ p }: { p: InstantPrediction }) {
  const [open, setOpen] = useState(false);
  const waCorrected = p.wa_model + p.bias;

  return (
    <div className="mt-3 pt-3" style={{ borderTop: "1px solid var(--color-rule)" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs font-medium"
        style={{ color: "var(--color-muted)", background: "none", border: "none", padding: 0, cursor: "pointer" }}
      >
        <svg
          className="w-3 h-3 flex-shrink-0"
          style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s" }}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        How this was calculated
      </button>

      {open && (
        <div className="mt-3 space-y-4">
          {/* Estimate source */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>
              Component estimate source
            </p>
            <p className="text-sm" style={{ color: "var(--color-ink)" }}>
              {p.src === "author"
                ? `Based on ${p.n_src} book${p.n_src !== 1 ? "s" : ""} by this author`
                : p.src === "genre"
                ? `Based on ${p.n_src} book${p.n_src !== 1 ? "s" : ""} in this genre`
                : `Global prior (${p.n_src} books) — no author or genre data`}
            </p>
          </div>

          {/* Computation chain */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest mb-2" style={{ color: "var(--color-muted)" }}>
              WA computation
            </p>
            <div
              className="rounded-lg p-3 space-y-1.5 text-xs"
              style={{ background: "var(--color-ground)", border: "1px solid var(--color-rule)", fontFamily: "var(--font-mono, monospace)" }}
            >
              <div className="flex justify-between gap-4">
                <span style={{ color: "var(--color-muted)" }}>Regression point estimate</span>
                <span style={{ color: "var(--color-ink)" }}>{p.wa_model.toFixed(3)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span style={{ color: "var(--color-muted)" }}>
                  Genre bias correction ({p.bias >= 0 ? "+" : ""}{p.bias.toFixed(3)})
                </span>
                <span style={{ color: p.bias >= 0 ? "var(--color-sage)" : "#C07C5A" }}>
                  {waCorrected.toFixed(3)}
                </span>
              </div>
              <div
                className="flex justify-between gap-4 pt-1.5"
                style={{ borderTop: "1px solid var(--color-rule)" }}
              >
                <span style={{ color: "var(--color-muted)" }}>
                  Analog mean (trust={p.trust.toFixed(2)}, blend {Math.round(p.trust * 100)}% model + {Math.round((1 - p.trust) * 100)}% analog)
                </span>
                <span style={{ color: "var(--color-ink)" }}>{p.analog_mean.toFixed(3)}</span>
              </div>
              <div
                className="flex justify-between gap-4 pt-1.5 font-semibold"
                style={{ borderTop: "1px solid var(--color-rule)", color: "var(--color-ink)" }}
              >
                <span>Final WA</span>
                <span>{p.wa_final.toFixed(3)}</span>
              </div>
            </div>
            <p className="text-xs mt-1.5" style={{ color: "var(--color-faint)" }}>
              Model R²={p.r2.toFixed(3)} · residual SD={p.resid_sd.toFixed(3)} · 90% CI = final WA ± 1.645 × SD
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Divider ─────────────────────────────────────────────────────────────── */
function Divider() {
  return <div className="my-6" style={{ borderTop: "1px solid var(--color-rule)" }} />;
}

/* ── Mode tab ────────────────────────────────────────────────────────────── */
function ModeTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
      style={{
        background: active ? "var(--color-sage)" : "var(--color-surface)",
        color: active ? "#fff" : "var(--color-muted)",
        border: active ? "none" : "1px solid var(--color-rule)",
      }}
    >
      {children}
    </button>
  );
}

/* ── Section card wrapper ────────────────────────────────────────────────── */
function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl p-5 ${className ?? ""}`}
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      {children}
    </div>
  );
}

/* ── Sage button ─────────────────────────────────────────────────────────── */
function SageButton({
  onClick,
  disabled,
  children,
  variant = "primary",
}: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  variant?: "primary" | "secondary";
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
      style={
        variant === "primary"
          ? { background: "var(--color-sage)", color: "#fff" }
          : {
              background: "var(--color-surface)",
              color: "var(--color-muted)",
              border: "1px solid var(--color-rule)",
            }
      }
    >
      {children}
    </button>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg px-4 py-3 text-sm"
      style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}
    >
      {message}
    </div>
  );
}

function InfoBox({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg px-4 py-3 text-sm"
      style={{
        background: "var(--color-sage-light)",
        color: "var(--color-sage)",
        border: "1px solid var(--color-sage)",
      }}
    >
      {message}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   PREDICT MODE
   ═══════════════════════════════════════════════════════════════════════════ */

const GENRE_AUTO = "✨ Auto-detect";

function PredictMode({
  books,
  validGenres,
  categoryOrder,
}: {
  books: Book[];
  validGenres: string[];
  categoryOrder: string[];
}) {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [genreChoice, setGenreChoice] = useState<string>(GENRE_AUTO);

  const [instant, setInstant] = useState<InstantPrediction | null>(null);
  const [instantError, setInstantError] = useState<string | null>(null);
  const [instantLoading, setInstantLoading] = useState(false);

  const [research, setResearch] = useState<ResearchResult | null>(null);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);

  // Auto-run instant when title + author + explicit genre are set
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const genre = genreChoice === GENRE_AUTO ? null : genreChoice;
    if (!title.trim() || !author.trim() || !genre) {
      setInstant(null);
      setInstantError(null);
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setInstantLoading(true);
      setInstantError(null);
      predictInstant(title.trim(), author.trim(), genre)
        .then(setInstant)
        .catch((e: unknown) =>
          setInstantError(e instanceof Error ? e.message : "Instant prediction failed.")
        )
        .finally(() => setInstantLoading(false));
    }, 600);
  }, [title, author, genreChoice]);

  // Clear research when inputs change
  useEffect(() => {
    setResearch(null);
    setResearchError(null);
  }, [title, author, genreChoice]);

  async function handleResearch() {
    if (!title.trim() || !author.trim()) return;
    setResearchLoading(true);
    setResearchError(null);
    setResearch(null);
    try {
      const genre = genreChoice === GENRE_AUTO ? undefined : genreChoice;
      const result = await predictResearch(title.trim(), author.trim(), genre);
      setResearch(result);
    } catch (e: unknown) {
      setResearchError(e instanceof Error ? e.message : "Research failed.");
    } finally {
      setResearchLoading(false);
    }
  }

  const nGenreInstant = instant?.n_genre ?? 0;

  return (
    <div className="space-y-6">
      {/* Inputs */}
      <Card>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <FieldLabel>Title</FieldLabel>
            <input
              type="text"
              className={inputCls}
              style={inputStyle}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. The Name of the Wind"
            />
          </div>
          <div>
            <FieldLabel>Author</FieldLabel>
            <input
              type="text"
              className={inputCls}
              style={inputStyle}
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder="e.g. Patrick Rothfuss"
            />
          </div>
          <div>
            <FieldLabel>Genre</FieldLabel>
            <select
              className={inputCls}
              style={inputStyle}
              value={genreChoice}
              onChange={(e) => setGenreChoice(e.target.value)}
            >
              <option value={GENRE_AUTO}>{GENRE_AUTO}</option>
              {validGenres.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-xs mt-3" style={{ color: "var(--color-muted)" }}>
          Leave genre on Auto-detect to use only title + author — grounded research will
          pick the genre from your list. Set a genre manually to see the instant estimate
          immediately.
        </p>
      </Card>

      {/* Instant estimate */}
      <div>
        <h2
          className="font-display font-semibold text-lg mb-1"
          style={{ color: "var(--color-ink)" }}
        >
          Instant estimate
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          Free quick-look from your analogs — no API call. Appears as soon as title,
          author, and genre are set.
        </p>

        {genreChoice === GENRE_AUTO && (title || author) && (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            Pick a genre above to see the instant estimate, or use Grounded research below
            to auto-detect.
          </p>
        )}
        {instantLoading && (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            Calculating…
          </p>
        )}
        {instantError && <ErrorBox message={instantError} />}
        {instant && !instantLoading && (
          <Card>
            <WACard
              wa={instant.wa_final}
              ci={instant.ci}
              rank={instant.rank}
              total={instant.total}
              label="Predicted WA"
              rankRange={instant.rank_range}
            />
            {nGenreInstant < 5 && (
              <div
                className="mt-3 rounded-lg px-3 py-2 text-xs"
                style={{ background: "#FFFBEB", border: "1px solid #FCD34D", color: "#92400E" }}
              >
                Thin genre (n={nGenreInstant}) — only {nGenreInstant} rated book{nGenreInstant !== 1 ? "s" : ""} in this genre. Treat as rough.
              </div>
            )}
            <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
              {Object.entries(instant.wcats).map(([cat, val]) => (
                <div key={cat} className="comp-tile">
                  <span className="comp-label">{cat}</span>
                  <span className="comp-value">{val.toFixed(2)}</span>
                </div>
              ))}
            </div>
            <InstantDecomposition p={instant} />
          </Card>
        )}
      </div>

      <Divider />

      {/* Grounded research */}
      <div>
        <h2
          className="font-display font-semibold text-lg mb-1"
          style={{ color: "var(--color-ink)" }}
        >
          Grounded research
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          Scores this specific book with a detailed rubric, then corrects onto your scale
          using your rated books in the same genre and by the same author. One API call
          (or cache hit — free).
        </p>

        <SageButton
          onClick={handleResearch}
          disabled={researchLoading || !title.trim() || !author.trim()}
        >
          {researchLoading ? "Researching… (one API call)" : "Research this book"}
        </SageButton>

        {researchError && <div className="mt-4"><ErrorBox message={researchError} /></div>}

        {research && (
          <div className="mt-6 space-y-5">
            {/* Book identity */}
            <div className="flex items-center gap-3 flex-wrap">
              <span className="font-display font-bold text-lg" style={{ color: "var(--color-ink)" }}>
                {research.title}
              </span>
              <span style={{ color: "var(--color-muted)" }}>by {research.author}</span>
              <span className="genre-chip">{research.genre}</span>
              {research.genre_auto_detected && (
                <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                  · genre auto-detected
                </span>
              )}
              {research.from_cache && (
                <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                  · reused cached research (no API call)
                </span>
              )}
            </div>

            <Card>
              <WACard
                wa={research.wa}
                ci={research.ci}
                rank={research.rank}
                total={research.total}
                label="Predicted WA (corrected)"
              />
            </Card>

            {/* PRIMARY: grounding signal */}
            <div>
              <p
                className="text-xs font-semibold uppercase tracking-widest mb-2"
                style={{ color: "var(--color-muted)" }}
              >
                Prediction reliability
              </p>
              <GroundingBadge nGenre={research.n_genre} nAuthor={research.n_author} />
              <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
                Model self-confidence: {research.conf} — the model's own assessment of
                how well it knows this book. Less reliable than the data-grounding signal above.
              </p>
            </div>

            {/* Corrected components */}
            <div>
              <p
                className="text-xs font-semibold uppercase tracking-widest mb-3"
                style={{ color: "var(--color-muted)" }}
              >
                Corrected component scores (author + genre corrected)
              </p>
              <ComponentGrid
                components={research.components}
                categoryOrder={research.category_order ?? categoryOrder}
              />
            </div>

            {research.blurb && (
              <div>
                <p
                  className="text-xs font-semibold uppercase tracking-widest mb-1"
                  style={{ color: "var(--color-muted)" }}
                >
                  Blurb
                </p>
                <p className="text-sm" style={{ color: "var(--color-ink)" }}>
                  {research.blurb}
                </p>
              </div>
            )}
            {research.keywords && (
              <div>
                <p
                  className="text-xs font-semibold uppercase tracking-widest mb-1"
                  style={{ color: "var(--color-muted)" }}
                >
                  Keywords
                </p>
                <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {research.keywords}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   DISCOVER MODE
   ═══════════════════════════════════════════════════════════════════════════ */

function DiscoverMode({
  books,
  categoryOrder,
}: {
  books: Book[];
  categoryOrder: string[];
}) {
  const [request, setRequest] = useState("");
  const [maxCandidates, setMaxCandidates] = useState(8);

  // Step 1: generate candidates
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [requestLabel, setRequestLabel] = useState("");
  const [genNote, setGenNote] = useState("");
  const [genSources, setGenSources] = useState<string[]>([]);
  const [genLoading, setGenLoading] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  // Step 2: scoring (runs sequentially, one per candidate)
  const [scored, setScored] = useState<ScoredCandidate[]>([]);
  const [scoringIdx, setScoringIdx] = useState<number | null>(null); // which candidate is being scored now
  const [scoringDone, setScoringDone] = useState(false);

  // Step 3: save
  const [toSave, setToSave] = useState<Set<string>>(new Set());
  const [saveResults, setSaveResults] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  async function handleGenerate() {
    if (!request.trim()) return;
    setGenLoading(true);
    setGenError(null);
    setGenNote("");
    setGenSources([]);
    setCandidates(null);
    setScored([]);
    setScoringDone(false);
    setToSave(new Set());
    setSaveResults({});
    try {
      const result = await discoverCandidates(request.trim(), maxCandidates);
      setCandidates(result.candidates);
      setRequestLabel(result.request);
      setGenNote(result.note ?? "");
      setGenSources(result.sources ?? []);
    } catch (e: unknown) {
      setGenError(e instanceof Error ? e.message : "Generation failed.");
    } finally {
      setGenLoading(false);
    }
  }

  async function handleScore() {
    if (!candidates || candidates.length === 0) return;
    setScored([]);
    setScoringDone(false);
    const results: ScoredCandidate[] = [];
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      setScoringIdx(i);
      try {
        const res = await predictResearch(c.title, c.author, c.genre ?? undefined);
        results.push(res);
      } catch (e: unknown) {
        results.push({
          title: c.title, author: c.author, genre: c.genre ?? "",
          wa: 0, ci: [0, 0], rank: 0, total: 0,
          n_genre: 0, n_author: 0, conf: "?",
          from_cache: false, words: null, series: "", series_number: null,
          blurb: "", keywords: "",
          components: {}, category_order: [],
          genre_auto_detected: false,
          error: e instanceof Error ? e.message : "Scoring failed",
        });
      }
      setScored([...results]);
    }
    setScoringIdx(null);
    setScoringDone(true);
  }

  const nCached = candidates?.filter((c) => c.cached).length ?? 0;
  const nNew = (candidates?.length ?? 0) - nCached;
  const okScored = scored.filter((r) => !r.error).sort((a, b) => b.wa - a.wa);
  const failedScored = scored.filter((r) => !!r.error);

  function toggleSave(title: string) {
    setToSave((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      return next;
    });
  }

  async function handleSave() {
    if (toSave.size === 0) return;
    setSaving(true);
    const newResults: Record<string, string> = {};
    for (const r of okScored) {
      if (!toSave.has(r.title)) continue;
      const flatScores: Record<string, number> = {};
      for (const comps of Object.values(r.components)) {
        for (const [comp, val] of Object.entries(comps)) {
          if (val !== null) flatScores[comp] = val;
        }
      }
      try {
        const res = await saveRecommendation({
          title: r.title, genre: r.genre, author: r.author,
          scores: flatScores,
          words: r.words ?? undefined,
          series: r.series || undefined,
          series_number: r.series_number ?? undefined,
          blurb: r.blurb || undefined,
          keywords: r.keywords || undefined,
        });
        newResults[r.title] = res.message || "Saved.";
      } catch (e: unknown) {
        newResults[r.title] = `Error: ${e instanceof Error ? e.message : "Failed"}`;
      }
    }
    setSaveResults(newResults);
    setSaving(false);
    setToSave(new Set());
  }

  return (
    <div className="space-y-6">
      {/* Request input */}
      <Card>
        <h2
          className="font-display font-semibold text-base mb-1"
          style={{ color: "var(--color-ink)" }}
        >
          What are you in the mood for?
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          Ask in plain language. The LLM proposes candidates — avoiding what you've already
          read — then your engine scores and ranks each one.
        </p>
        <textarea
          className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 resize-none"
          style={{ ...inputStyle, minHeight: "4rem" }}
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder={
            "e.g. recommend 5 epic fantasy books · something like Toll the Hounds " +
            "but in a different genre · underrated sci-fi from the 2010s"
          }
        />
        <div className="flex items-center gap-4 mt-3">
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>
              Max candidates
            </label>
            <input
              type="number"
              min={1}
              max={15}
              value={maxCandidates}
              onChange={(e) => setMaxCandidates(parseInt(e.target.value) || 8)}
              className="w-16 px-2 py-1.5 rounded-lg text-sm border focus:outline-none"
              style={inputStyle}
            />
          </div>
          <SageButton
            onClick={handleGenerate}
            disabled={genLoading || !request.trim()}
          >
            {genLoading ? "Generating candidates…" : "Generate candidates"}
          </SageButton>
        </div>
        {genError && <div className="mt-3"><ErrorBox message={genError} /></div>}
      </Card>

      {/* Candidate list + confirm */}
      {candidates && candidates.length === 0 && (
        <InfoBox message={genNote || "The model didn't return any fresh candidates — try rephrasing or widening the request."} />
      )}

      {candidates && candidates.length > 0 && genNote && (
        <InfoBox message={genNote} />
      )}

      {candidates && candidates.length > 0 && (
        <Card>
          <p
            className="font-semibold text-sm mb-3"
            style={{ color: "var(--color-ink)" }}
          >
            Candidates for: <em>{requestLabel}</em>
          </p>
          <SortableTable<Candidate>
            columns={CANDIDATE_COLS}
            data={candidates}
            defaultSort={{
              key: candidates.some((c) => c.series_number != null) ? "series_number" : "title",
              dir: "asc",
            }}
            getRowKey={(c) => c.title}
          />
          <p className="text-xs mt-3" style={{ color: "var(--color-muted)" }}>
            {nCached} already researched (free) · {nNew} new (~1¢ and a few seconds each)
          </p>
          {genSources.length > 0 && (
            <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
              Series data from Goodreads:{" "}
              {genSources.slice(0, 3).map((u, i) => (
                <span key={u}>
                  {i > 0 ? " · " : ""}
                  <a href={u} target="_blank" rel="noreferrer" style={{ textDecoration: "underline" }}>
                    {u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 48)}
                  </a>
                </span>
              ))}
            </p>
          )}

          {scoringIdx === null && !scoringDone && (
            <div className="mt-4">
              <SageButton onClick={handleScore} disabled={scoringIdx !== null}>
                Confirm & score {candidates.length} candidate
                {candidates.length !== 1 ? "s" : ""}
              </SageButton>
            </div>
          )}

          {scoringIdx !== null && (
            <div className="mt-4">
              <div
                className="rounded-full h-2 overflow-hidden"
                style={{ background: "var(--color-rule)" }}
              >
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    background: "var(--color-sage)",
                    width: `${((scoringIdx + 1) / candidates.length) * 100}%`,
                  }}
                />
              </div>
              <p className="text-xs mt-1" style={{ color: "var(--color-muted)" }}>
                Scoring {scoringIdx + 1} / {candidates.length}: {candidates[scoringIdx].title}
              </p>
            </div>
          )}
        </Card>
      )}

      {/* Scored results */}
      {okScored.length > 0 && (
        <div className="space-y-4">
          <h2
            className="font-display font-semibold text-lg"
            style={{ color: "var(--color-ink)" }}
          >
            Discovered books — ranked by your predicted WA
          </h2>
          <p className="text-xs -mt-2" style={{ color: "var(--color-muted)" }}>
            Grounding is the primary reliability signal. Strong = many genre books or ≥1 by
            this author. Model self-confidence shown separately as a secondary note.
          </p>

          {okScored.map((r, i) => (
            <ScoredCard
              key={r.title}
              result={r}
              rank={i + 1}
              categoryOrder={r.category_order?.length ? r.category_order : categoryOrder}
              selected={toSave.has(r.title)}
              onToggle={() => toggleSave(r.title)}
              saveMsg={saveResults[r.title]}
              scoringDone={scoringDone}
            />
          ))}

          {failedScored.length > 0 && (
            <div
              className="rounded-lg px-4 py-3 text-sm"
              style={{ background: "#FFFBEB", border: "1px solid #FCD34D", color: "#92400E" }}
            >
              Could not score:{" "}
              {failedScored.map((r) => `${r.title} (${r.error})`).join(", ")}
            </div>
          )}

          {scoringDone && (
            <div className="flex items-center gap-3 pt-2">
              <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                {toSave.size > 0
                  ? `${toSave.size} book${toSave.size > 1 ? "s" : ""} selected to save`
                  : "Select books above to save to your recommendations (TBR)"}
              </p>
              {toSave.size > 0 && (
                <SageButton onClick={handleSave} disabled={saving}>
                  {saving ? "Saving…" : `Save ${toSave.size} to recommendations`}
                </SageButton>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Scored candidate card ───────────────────────────────────────────────── */
function ScoredCard({
  result,
  rank,
  categoryOrder,
  selected,
  onToggle,
  saveMsg,
  scoringDone,
}: {
  result: ScoredCandidate;
  rank: number;
  categoryOrder: string[];
  selected: boolean;
  onToggle: () => void;
  saveMsg?: string;
  scoringDone: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ border: "1px solid var(--color-rule)" }}
    >
      {/* Header row */}
      <div
        className="flex items-center gap-4 px-5 py-4 cursor-pointer"
        style={{ background: "var(--color-surface)" }}
        onClick={() => setOpen((o) => !o)}
      >
        <span
          className="font-display italic text-sm w-6 text-right flex-shrink-0"
          style={{ color: "var(--color-faint)" }}
        >
          {rank}
        </span>
        <div
          className="wa-badge flex-shrink-0"
          style={{ width: "2.5rem", height: "2.5rem", fontSize: "0.75rem" }}
        >
          {result.wa.toFixed(2)}
        </div>
        <div className="flex-1 min-w-0">
          <p
            className="font-display font-semibold text-base leading-tight truncate"
            style={{ color: "var(--color-ink)" }}
          >
            {result.title}
          </p>
          <p className="text-sm truncate" style={{ color: "var(--color-muted)" }}>
            {result.author}
          </p>
        </div>
        <div className="hidden sm:flex flex-col items-end gap-1 flex-shrink-0">
          <span className="genre-chip">{result.genre}</span>
          <span className="text-xs" style={{ color: "var(--color-faint)" }}>
            rank ~{result.rank} of {result.total}
          </span>
        </div>
        <svg
          className="w-4 h-4 flex-shrink-0 transition-transform"
          style={{
            color: "var(--color-faint)",
            transform: open ? "rotate(180deg)" : "none",
          }}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Expanded detail */}
      {open && (
        <div
          className="px-5 py-4 space-y-4"
          style={{
            borderTop: "1px solid var(--color-rule)",
            background: "var(--color-ground)",
          }}
        >
          <div className="flex gap-3 text-sm flex-wrap">
            <span style={{ color: "var(--color-ink)" }}>
              <strong>90% CI:</strong> {result.ci[0].toFixed(2)} – {result.ci[1].toFixed(2)}
            </span>
            {result.words && (
              <span style={{ color: "var(--color-muted)" }}>
                ~{result.words.toLocaleString()} words
              </span>
            )}
          </div>

          {/* PRIMARY: grounding */}
          <GroundingBadge nGenre={result.n_genre} nAuthor={result.n_author} />
          <p className="text-xs" style={{ color: "var(--color-faint)" }}>
            Model self-confidence: {result.conf} — less reliable than the grounding signal above.
          </p>

          <ComponentGrid components={result.components} categoryOrder={categoryOrder} />

          {result.blurb && (
            <p className="text-sm italic" style={{ color: "var(--color-muted)" }}>
              {result.blurb}
            </p>
          )}
          {result.keywords && (
            <p className="text-xs" style={{ color: "var(--color-faint)" }}>
              {result.keywords}
            </p>
          )}
        </div>
      )}

      {/* Save toggle row */}
      {scoringDone && (
        <div
          className="px-5 py-2 flex items-center justify-between"
          style={{
            borderTop: "1px solid var(--color-rule)",
            background: selected ? "var(--color-sage-light)" : "var(--color-surface)",
          }}
        >
          {saveMsg ? (
            <p className="text-xs" style={{ color: "var(--color-sage)" }}>
              ✓ {saveMsg}
            </p>
          ) : (
            <button
              onClick={onToggle}
              className="text-xs font-medium px-3 py-1 rounded-lg transition-colors"
              style={
                selected
                  ? { background: "var(--color-sage)", color: "#fff" }
                  : {
                      background: "transparent",
                      color: "var(--color-muted)",
                      border: "1px solid var(--color-rule)",
                    }
              }
            >
              {selected ? "✓ Selected for save" : "Save to recommendations"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ROOT PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════════════════ */

export default function PredictClient({
  books,
  validGenres,
  categoryOrder,
}: {
  books: Book[];
  validGenres: string[];
  categoryOrder: string[];
}) {
  const [mode, setMode] = useState<"predict" | "discover">("predict");

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Predict
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          Name a book to predict — or ask the LLM to discover candidates, then let your engine score them.
        </p>
      </div>

      {/* Mode switcher */}
      <div className="flex gap-2 mb-8">
        <ModeTab active={mode === "predict"} onClick={() => setMode("predict")}>
          Predict a book I name
        </ModeTab>
        <ModeTab active={mode === "discover"} onClick={() => setMode("discover")}>
          Discover books to predict
        </ModeTab>
      </div>

      {mode === "predict" ? (
        <PredictMode books={books} validGenres={validGenres} categoryOrder={categoryOrder} />
      ) : (
        <DiscoverMode books={books} categoryOrder={categoryOrder} />
      )}
    </div>
  );
}
