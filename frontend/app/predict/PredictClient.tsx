"use client";

import { useState } from "react";
import {
  predictResearch,
  discoverCandidates,
  saveRecommendation,
  predictNonfiction,
  saveNonfictionRecommendation,
  discoverNonfictionCandidates,
} from "@/lib/api";
import type {
  ResearchResult,
  Candidate,
  ScoredCandidate,
  NonfictionPrediction,
  NonfictionCandidate,
  BookKind,
} from "@/lib/types";

/** Flatten a grouped-by-category prediction's components into a flat score map. */
function flattenNfScores(components: NonfictionPrediction["components"]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const cat of Object.values(components)) {
    for (const [c, v] of Object.entries(cat)) if (v != null) out[c] = v;
  }
  return out;
}
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";

/* Bounded-concurrency async pool: run `fn` over `items` with at most `limit`
   promises in flight at once. STEP 5 uses it to grounded-refine several Discover
   candidates in parallel (each ~110s) instead of one-at-a-time, while capping
   concurrency to respect API rate limits. For large NON-interactive re-score
   jobs the Anthropic Message Batches API is the cheaper bulk path — not used
   here (this flow is interactive and small-N). */
async function mapPool<T>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<void>,
): Promise<void> {
  let next = 0;
  const run = async () => {
    while (true) {
      const i = next++;
      if (i >= items.length) break;
      await fn(items[i], i);
    }
  };
  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, run),
  );
}

/* Max grounded refines in flight at once. Raised from 4 to parallelize the
   unavoidable grounding of a genuinely-new series; the Anthropic SDK auto-retries
   429s with exponential backoff (honoring Retry-After), and a refine that still
   fails degrades to its memory score (re-runnable via the per-card Refine button),
   so a higher fan-out is safe. Bounded so a burst can't exhaust the rate limit. */
const REFINE_CONCURRENCY = 8;

/* How many top candidates (by predicted WA) are grounded-refined automatically after
   scoring. The rest refine ON DEMAND (per card, or "Refine all") so a large Discover
   run doesn't fire a slow ~tens-of-seconds web_search for every candidate up front —
   only for the handful the reader is most likely to care about. */
const EAGER_REFINE_K = 3;

/* Max recommendation saves in flight at once. Each /api/recommendations save is
   server-side ~2 LLM calls (series/ordinal lookup + rich house-style blurb,
   deferred from scoring so they're only paid for kept books), so saving a
   multi-book selection one-at-a-time was the slowest step in the flow. Bounded
   like REFINE_CONCURRENCY; the Anthropic SDK auto-retries 429s and each save is
   reported per-book, so a burst can't corrupt the batch. */
const SAVE_CONCURRENCY = 8;

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

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

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
   DISCOVER MODE
   ═══════════════════════════════════════════════════════════════════════════ */

function DiscoverMode({
  categoryOrder,
}: {
  categoryOrder: string[];
}) {
  const [request, setRequest] = useState("");

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
  const [refiningTitles, setRefiningTitles] = useState<Set<string>>(new Set()); // titles being grounded-refined now

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
    setRefiningTitles(new Set());
    setToSave(new Set());
    setSaveResults({});
    try {
      const result = await discoverCandidates(request.trim());
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
    setRefiningTitles(new Set());
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
          wa: 0, rank: 0, total: 0,
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
    // Eager pass: grounded-refine only the top few by predicted WA; the rest refine
    // on demand (per card, or "Refine all") so a large run doesn't fire N slow calls.
    const eager = [...results]
      .filter((r) => !r.error)
      .sort((a, b) => b.wa - a.wa)
      .slice(0, EAGER_REFINE_K);
    void refineSet(eager);
  }

  // Progressive grounded (hybrid) refine: re-score the given candidates with the
  // web-grounded upgrade and swap each in place as it lands, up to REFINE_CONCURRENCY
  // at once. Skips any already grounded, currently refining, errored, or with no
  // upgrade available — so it is safe to call repeatedly (eager top-K, per card, or
  // "Refine all"). The memory scores are already on screen, so the reader can act
  // immediately; grounded scores stream in and the list re-sorts. Functional
  // setState updaters compose safely under interleaving.
  async function refineSet(targets: ScoredCandidate[]) {
    const todo = targets.filter(
      (r) => !r.error && r.hybrid_available && r.sourcing !== "hybrid"
        && !refiningTitles.has(r.title),
    );
    if (todo.length === 0) return;
    setRefiningTitles((prev) => {
      const next = new Set(prev);
      todo.forEach((r) => next.add(r.title));
      return next;
    });
    await mapPool(todo, REFINE_CONCURRENCY, async (r) => {
      try {
        const g = await predictResearch(r.title, r.author, r.genre, true);
        setScored((prev) => prev.map((x) => (x.title === r.title ? { ...g } : x)));
      } catch {
        // keep the memory result if the grounded refine fails
      }
      setRefiningTitles((prev) => {
        const next = new Set(prev);
        next.delete(r.title);
        return next;
      });
    });
  }

  // Refine one candidate on demand (looks up its current scored object by title).
  function refineOne(title: string) {
    const r = scored.find((x) => x.title === title);
    if (r) void refineSet([r]);
  }

  // Refine every candidate not yet grounded (the "Refine all remaining" escape hatch).
  function refineRemaining() {
    void refineSet(scored);
  }

  const nCached = candidates?.filter((c) => c.cached).length ?? 0;
  const nNew = (candidates?.length ?? 0) - nCached;
  const okScored = scored.filter((r) => !r.error).sort((a, b) => b.wa - a.wa);
  const failedScored = scored.filter((r) => !!r.error);
  const refiningCount = refiningTitles.size;
  // Candidates that could still be grounded but haven't been (and aren't in flight).
  const unrefined = okScored.filter(
    (r) => r.hybrid_available && r.sourcing !== "hybrid" && !refiningTitles.has(r.title),
  );

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
    // Save the selected books with bounded concurrency instead of one-at-a-time:
    // each save costs ~2 server-side LLM calls, so a sequential loop stacked that
    // cost linearly. Distinct titles write distinct keys of `newResults`, so the
    // concurrent writes don't race (single-threaded event loop, one key each).
    const targets = okScored.filter((r) => toSave.has(r.title));
    const newResults: Record<string, string> = {};
    await mapPool(targets, SAVE_CONCURRENCY, async (r) => {
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
    });
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
          Ask in plain language. The LLM proposes candidates — avoiding what you&apos;ve already
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
        <p className="text-xs mt-2 mb-3" style={{ color: "var(--color-faint)" }}>
          State how many you want in your request (e.g. “the 5 main books of …”, “a few
          cozy mysteries”) — or name a single book to predict it directly.
        </p>
        <div className="flex items-center gap-4 mt-3">
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

          {(refiningCount > 0 || unrefined.length > 0) && (
            <div
              className="rounded-lg px-4 py-2 text-xs flex items-center gap-x-4 gap-y-1 flex-wrap"
              style={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-rule)",
                color: "var(--color-muted)",
              }}
            >
              {refiningCount > 0 && (
                <span className="flex items-center gap-2">
                  <span
                    className="inline-block w-2 h-2 rounded-full animate-pulse"
                    style={{ background: "var(--color-sage)" }}
                  />
                  Refining {refiningCount} with reviews — scores update live.
                </span>
              )}
              {unrefined.length > 0 && (
                <button
                  onClick={refineRemaining}
                  className="underline"
                  style={{ color: "var(--color-sage)" }}
                >
                  Refine {refiningCount > 0 ? `${unrefined.length} more` : `all ${unrefined.length}`}
                  {" "}with reviews
                </button>
              )}
              <span style={{ color: "var(--color-faint)" }}>
                Top {EAGER_REFINE_K} refined automatically · you can save anytime.
              </span>
            </div>
          )}

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
              refining={refiningTitles.has(r.title)}
              onRefine={() => refineOne(r.title)}
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
  refining,
  onRefine,
}: {
  result: ScoredCandidate;
  rank: number;
  categoryOrder: string[];
  selected: boolean;
  onToggle: () => void;
  saveMsg?: string;
  scoringDone: boolean;
  refining?: boolean;
  onRefine?: () => void;
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
        {result.sourcing === "hybrid" ? (
          <span
            className="text-xs flex-shrink-0 hidden sm:inline"
            style={{ color: "var(--color-sage)" }}
            title="Grounded with reader reviews"
          >
            ✓ reviews
          </span>
        ) : refining ? (
          <span
            className="text-xs flex-shrink-0 animate-pulse"
            style={{ color: "var(--color-muted)" }}
          >
            refining…
          </span>
        ) : result.hybrid_available && onRefine ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRefine();
            }}
            className="text-xs px-2 py-1 rounded-md flex-shrink-0"
            style={{ border: "1px solid var(--color-rule)", color: "var(--color-sage)" }}
            title="Ground this book's scores with reader reviews"
          >
            Refine
          </button>
        ) : null}
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
            {result.words && (
              <span style={{ color: "var(--color-muted)" }}>
                ~{result.words.toLocaleString()} words
              </span>
            )}
          </div>

          {/* PRIMARY: grounding */}
          <GroundingBadge nGenre={result.n_genre} nAuthor={result.n_author} />

          {/* Empirical 80% interval from LOO residuals at this data density.
              Secondary to grounding, per the display decision. */}
          {result.wa_low != null && result.wa_high != null && (
            <p className="text-sm" style={{ color: "var(--color-ink)" }}>
              <strong>{result.wa.toFixed(1)}</strong>{" "}
              <span style={{ color: "var(--color-muted)" }}>
                ({result.wa_low.toFixed(1)}–{result.wa_high.toFixed(1)}, 80% interval)
              </span>
              {result.bucket_label && (
                <span style={{ color: "var(--color-faint)" }}>
                  {" · "}{result.bucket_label}
                  {result.pooled && " (pooled)"}
                  {result.stale && " · stale"}
                </span>
              )}
            </p>
          )}

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
   NONFICTION PREDICT MODE — name a book, grounded LLM scores it, rolled up
   through the nonfiction engine and ranked by Total Average. No TBR save
   (there is no nonfiction recommendations table).
   ═══════════════════════════════════════════════════════════════════════════ */

function NonfictionPredictMode() {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NonfictionPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function run() {
    if (!title.trim() || !author.trim()) {
      setError("Enter a title and author.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    setSaved(null);
    setSaveError(null);
    try {
      setResult(await predictNonfiction(title.trim(), author.trim()));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Prediction failed.");
    } finally {
      setLoading(false);
    }
  }

  async function saveToTbr() {
    if (!result) return;
    setSaving(true);
    setSaveError(null);
    try {
      const r = await saveNonfictionRecommendation({
        title: result.title, author: result.author, scores: flattenNfScores(result.components),
      });
      setSaved(r.message || "Saved to your nonfiction TBR.");
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <h2 className="font-display font-semibold text-base mb-1" style={{ color: "var(--color-ink)" }}>
          Predict a nonfiction book
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          Name a book — one grounded LLM call scores the 8 nonfiction components, then your engine
          rolls them up to a Quality-lean WA and ranks by Total Average against your rated nonfiction.
        </p>
        <div className="flex flex-wrap gap-3">
          <div className="flex-1 min-w-48">
            <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Sapiens"
              className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2" style={inputStyle} />
          </div>
          <div className="flex-1 min-w-40">
            <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Author</label>
            <input type="text" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="e.g. Yuval Noah Harari"
              className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2" style={inputStyle} />
          </div>
          <div className="flex items-end">
            <SageButton onClick={run} disabled={loading}>{loading ? "Researching…" : "Research & predict"}</SageButton>
          </div>
        </div>
      </Card>

      {error && <ErrorBox message={error} />}

      {result && (
        <Card>
          <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
            <div>
              <p className="font-display font-bold text-lg leading-tight" style={{ color: "var(--color-ink)" }}>{result.title}</p>
              <p className="text-sm" style={{ color: "var(--color-muted)" }}>{result.author} · Nonfiction · confidence {result.confidence}</p>
            </div>
            <span className="wa-badge">{result.total_average.toFixed(2)}</span>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 mb-4 text-sm">
            <span style={{ color: "var(--color-muted)" }}>Total Average <b style={{ color: "var(--color-sage)" }}>{result.total_average.toFixed(2)}</b></span>
            <span style={{ color: "var(--color-muted)" }}>WA <b style={{ color: "var(--color-ink)" }}>{result.wa.toFixed(2)}</b></span>
            <span style={{ color: "var(--color-muted)" }}>Predicted rank <b style={{ color: "var(--color-ink)" }}>~{result.rank} of {result.total}</b></span>
          </div>
          <ComponentGrid components={result.components} categoryOrder={result.category_order} />
          <div className="mt-4">
            <InfoBox message={`Low confidence — only ${result.total} nonfiction books rated, so this leans on priors. Treat as a rough estimate until the library grows.`} />
          </div>
          <div className="mt-4 flex items-center gap-3 flex-wrap">
            {saved ? (
              <span className="text-sm font-medium" style={{ color: "var(--color-sage)" }}>✓ {saved}</span>
            ) : (
              <SageButton onClick={saveToTbr} disabled={saving}>{saving ? "Saving…" : "Save to TBR"}</SageButton>
            )}
            {saveError && <span className="text-sm" style={{ color: "#B91C1C" }}>{saveError}</span>}
          </div>
        </Card>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   NONFICTION DISCOVER — brainstorm candidates (cheap), research each (Opus),
   save keepers to the nonfiction TBR.
   ═══════════════════════════════════════════════════════════════════════════ */

function CandidateCard({ candidate }: { candidate: NonfictionCandidate }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NonfictionPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function research() {
    setLoading(true);
    setError(null);
    try {
      setResult(await predictNonfiction(candidate.title, candidate.author));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Research failed.");
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (!result) return;
    setSaving(true);
    setError(null);
    try {
      await saveNonfictionRecommendation({
        title: result.title, author: result.author, scores: flattenNfScores(result.components),
      });
      setSaved(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-display font-semibold text-sm" style={{ color: "var(--color-ink)" }}>{candidate.title}</p>
          <p className="text-xs" style={{ color: "var(--color-muted)" }}>{candidate.author}</p>
        </div>
        {!result && <SageButton onClick={research} disabled={loading}>{loading ? "Researching…" : "Research & score"}</SageButton>}
      </div>
      {error && <div className="mt-3"><ErrorBox message={error} /></div>}
      {result && (
        <div className="mt-3">
          <div className="flex flex-wrap gap-x-6 gap-y-1 mb-3 text-sm">
            <span style={{ color: "var(--color-muted)" }}>Total Average <b style={{ color: "var(--color-sage)" }}>{result.total_average.toFixed(2)}</b></span>
            <span style={{ color: "var(--color-muted)" }}>WA <b style={{ color: "var(--color-ink)" }}>{result.wa.toFixed(2)}</b></span>
            <span style={{ color: "var(--color-muted)" }}>rank <b style={{ color: "var(--color-ink)" }}>~{result.rank} of {result.total}</b></span>
            <span style={{ color: "var(--color-faint)" }}>confidence {result.confidence}</span>
          </div>
          <ComponentGrid components={result.components} categoryOrder={result.category_order} />
          <div className="mt-3">
            {saved ? (
              <span className="text-sm font-medium" style={{ color: "var(--color-sage)" }}>✓ Saved to TBR</span>
            ) : (
              <SageButton onClick={save} disabled={saving}>{saving ? "Saving…" : "Save to TBR"}</SageButton>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function NonfictionDiscoverMode() {
  const [request, setRequest] = useState("");
  const [loading, setLoading] = useState(false);
  const [candidates, setCandidates] = useState<NonfictionCandidate[] | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function find() {
    if (!request.trim()) {
      setError("Enter a request.");
      return;
    }
    setLoading(true);
    setError(null);
    setCandidates(null);
    setNote("");
    try {
      const r = await discoverNonfictionCandidates(request.trim());
      setCandidates(r.candidates);
      setNote(r.note ?? "");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Discover failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <h2 className="font-display font-semibold text-base mb-1" style={{ color: "var(--color-ink)" }}>Discover nonfiction</h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          Describe what you want — one cheap call brainstorms real nonfiction books (excluding ones
          already in your library or TBR). Research each to score it, then save the keepers.
        </p>
        <div className="flex flex-wrap gap-3">
          <div className="flex-1 min-w-64">
            <input
              type="text"
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") find(); }}
              placeholder="e.g. books on behavioral economics and decision-making"
              className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
              style={inputStyle}
            />
          </div>
          <div className="flex items-end"><SageButton onClick={find} disabled={loading}>{loading ? "Finding…" : "Find books"}</SageButton></div>
        </div>
      </Card>
      {error && <ErrorBox message={error} />}
      {note && <InfoBox message={note} />}
      {candidates && candidates.length === 0 && !note && (
        <InfoBox message="No candidates came back — try rephrasing the request." />
      )}
      {candidates && candidates.map((c) => <CandidateCard key={`${c.title}::${c.author}`} candidate={c} />)}
    </div>
  );
}

function NonfictionMode() {
  const [sub, setSub] = useState<"discover" | "named">("discover");
  return (
    <div>
      <div className="flex gap-1 mb-6 p-1 rounded-xl inline-flex" style={{ background: "var(--color-surface-2)" }}>
        {([["discover", "Discover"], ["named", "Name a book"]] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setSub(id)}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: sub === id ? "var(--color-surface)" : "transparent",
              color: sub === id ? "var(--color-sage)" : "var(--color-muted)",
              boxShadow: sub === id ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {sub === "discover" ? <NonfictionDiscoverMode /> : <NonfictionPredictMode />}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ROOT PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════════════════ */

export default function PredictClient({
  categoryOrder,
}: {
  categoryOrder: string[];
}) {
  const [kind, setKind] = useState<BookKind>("fiction");
  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Predict
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {kind === "nonfiction"
            ? "Discover nonfiction books — or name one — then let your engine predict where they land."
            : "Ask the LLM to discover candidates — or name a single book — then let your engine score and rank them."}
        </p>
      </div>

      {/* Fiction / Nonfiction toggle */}
      <div className="flex gap-1 mb-8 p-1 rounded-xl inline-flex" style={{ background: "var(--color-surface-2)" }}>
        {(["fiction", "nonfiction"] as BookKind[]).map((k) => (
          <button
            key={k}
            onClick={() => setKind(k)}
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

      {kind === "fiction" ? <DiscoverMode categoryOrder={categoryOrder} /> : <NonfictionMode />}
    </div>
  );
}
