"use client";

import { useMemo, useState } from "react";
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
  BookKind,
} from "@/lib/types";

/** Flatten a scored book's grouped-by-category components into the flat score map
 *  the save endpoints expect. Shared by the fiction and nonfiction save paths. */
function flattenScores(components: ScoredCandidate["components"]): Record<string, number> {
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

/* Max grounded refines in flight at once. MEASURED, not assumed (2026-07-21):
   grounding 15 cold books took 333s at concurrency 8 but 386s at 15 — the wider
   fan-out is 16% SLOWER, not faster. Each grounded call is one Opus web_search
   turn, and firing more at once trips the server rate limiter harder: at 15,
   10/15 calls ate a ~60s Retry-After backoff (vs 8/15 at concurrency 8) and one
   badly-throttled straggler in the single big wave set the whole batch's
   wall-clock. So "one wave instead of two" is a false economy here — the one
   wave is throttle-bound. 8 is the measured-better default; the rate limiter
   starts biting around ~5-6 concurrent (the first few calls in any wave clear
   at the ~30s fast path, the rest queue behind backoff), so lower may be
   marginally better still, but 8 is the known-good value. Do NOT raise without
   re-measuring total wall — per-call latency is NOT independent of concurrency. */
const REFINE_CONCURRENCY = 8;

/* How many top candidates (by predicted WA) are grounded-refined automatically after
   scoring. The rest refine ON DEMAND (per card, or "Refine all"). Raised to 10 to match
   the opt-out save flow — the reader now keeps most candidates, so ground most of them
   up front rather than leaving them base-only. Still capped so a max-size Discover run
   (DISCOVER_MAX=15) doesn't fire a slow ~tens-of-seconds web_search for EVERY candidate;
   the eager batch runs bounded at REFINE_CONCURRENCY and progressively (scores stream in). */
const EAGER_REFINE_K = 10;

/* Max recommendation saves in flight at once. Each /api/recommendations save is
   server-side ~2 LLM calls (series/ordinal lookup + rich house-style blurb,
   deferred from scoring so they're only paid for kept books), so saving a
   multi-book selection one-at-a-time was the slowest step in the flow. Bounded
   like REFINE_CONCURRENCY; the Anthropic SDK auto-retries 429s and each save is
   reported per-book, so a burst can't corrupt the batch. */
const SAVE_CONCURRENCY = 8;

/* ── Candidate table columns ─────────────────────────────────────────────── */

const CANDIDATE_COLS: ColDef<Candidate>[] = [
  { key: "title",  label: "Title",  type: "string", getValue: (c) => c.title,
    formatter: (v, c) => c.requested
      ? <span>{v}{" "}<span
          className="ml-1 px-1.5 py-0.5 rounded text-[0.65rem] font-semibold uppercase tracking-wide align-middle"
          style={{ background: "var(--color-sage-light)", color: "var(--color-sage)" }}
        >your pick</span></span>
      : <>{v}</> },
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

function PredictFlow({ config }: { config: PredictFlowConfig }) {
  const { categoryOrder } = config;
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

  // Step 3: save. Opt-out model — every scored book is queued to save unless the
  // reader removes it with the ✕ (`removed`), so a "save most of them" run is one click.
  const [removed, setRemoved] = useState<Set<string>>(new Set());
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
    setRemoved(new Set());
    setSaveResults({});
    try {
      const result = await config.discover(request.trim());
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
    setRemoved(new Set());
    setSaveResults({});
    const results: ScoredCandidate[] = [];
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      setScoringIdx(i);
      try {
        const res = await config.score(c);
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
    // Eager pass (fiction only — nonfiction has no grounded upgrade): grounded-refine
    // only the top few by predicted score; the rest refine on demand (per card, or
    // "Refine all") so a large run doesn't fire N slow calls.
    if (config.refine) {
      const eager = [...results]
        .filter((r) => !r.error)
        .sort((a, b) => config.primaryScore(b) - config.primaryScore(a))
        .slice(0, EAGER_REFINE_K);
      void refineSet(eager);
    }
  }

  // Progressive grounded (hybrid) refine: re-score the given candidates with the
  // web-grounded upgrade and swap each in place as it lands, up to REFINE_CONCURRENCY
  // at once. Skips any already grounded, currently refining, errored, or with no
  // upgrade available — so it is safe to call repeatedly (eager top-K, per card, or
  // "Refine all"). The memory scores are already on screen, so the reader can act
  // immediately; grounded scores stream in and the list re-sorts. Functional
  // setState updaters compose safely under interleaving.
  async function refineSet(targets: ScoredCandidate[]) {
    const refine = config.refine;
    if (!refine) return;   // nonfiction has no grounded-refine path
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
        const g = await refine(r);
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
  // Skips books the reader removed with ✕ — no point spending a web_search on a discard.
  function refineRemaining() {
    void refineSet(scored.filter((r) => !removed.has(r.title)));
  }

  const nCached = candidates?.filter((c) => c.cached).length ?? 0;
  const nNew = (candidates?.length ?? 0) - nCached;
  const okScored = scored
    .filter((r) => !r.error)
    .sort((a, b) => config.primaryScore(b) - config.primaryScore(a));
  const failedScored = scored.filter((r) => !!r.error);
  // Opt-out save model: every scored book is queued to save UNLESS the reader
  // removed it with the ✕. `kept` is what's shown + saved; `savable` also excludes
  // ones already saved so re-clicking Save doesn't re-POST (and hit a dup refusal).
  const kept = okScored.filter((r) => !removed.has(r.title));
  const savable = kept.filter((r) => !saveResults[r.title]);
  const nRemoved = removed.size;
  const refiningCount = refiningTitles.size;
  // Candidates that could still be grounded but haven't been (and aren't in flight).
  // Excludes removed books — no point grounding something the reader discarded.
  const unrefined = kept.filter(
    (r) => r.hybrid_available && r.sourcing !== "hybrid" && !refiningTitles.has(r.title),
  );

  // Opt-out: ✕ on a card drops it from the save set; everything else still saves.
  function removeBook(title: string) {
    setRemoved((prev) => new Set(prev).add(title));
  }
  function restoreAll() {
    setRemoved(new Set());
  }

  async function handleSave() {
    if (savable.length === 0) return;
    setSaving(true);
    // Opt-out save: everything scored is queued unless the reader removed it with ✕.
    // Save the kept, not-yet-saved books with bounded concurrency instead of
    // one-at-a-time — each save costs ~2 server-side LLM calls, so a sequential loop
    // stacked that cost linearly. Distinct titles write distinct keys of `newResults`,
    // so the concurrent writes don't race (single-threaded event loop, one key each).
    const targets = savable;
    const newResults: Record<string, string> = {};
    await mapPool(targets, SAVE_CONCURRENCY, async (r) => {
      try {
        const res = await config.save(r);
        newResults[r.title] = res.message || "Saved.";
      } catch (e: unknown) {
        newResults[r.title] = `Error: ${e instanceof Error ? e.message : "Failed"}`;
      }
    });
    // Merge (don't replace) so an earlier batch's ✓ results survive a second save.
    setSaveResults((prev) => ({ ...prev, ...newResults }));
    setSaving(false);
  }

  return (
    <div className="space-y-6">
      {/* Request input */}
      <Card>
        <h2
          className="font-display font-semibold text-base mb-1"
          style={{ color: "var(--color-ink)" }}
        >
          {config.requestTitle}
        </h2>
        <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
          {config.requestHelp}
        </p>
        <textarea
          className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 resize-none"
          style={{ ...inputStyle, minHeight: "4rem" }}
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder={config.placeholder}
        />
        <p className="text-xs mt-2 mb-3" style={{ color: "var(--color-faint)" }}>
          {config.countHint}
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
            pinFirst={(c) => !!c.requested}
          />
          <p className="text-xs mt-3" style={{ color: "var(--color-muted)" }}>
            {nCached} already researched (free) · {nNew} new ({config.newCostHint})
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
            Discovered books — ranked by your predicted {config.primaryLabel}
          </h2>
          <p className="text-xs -mt-2" style={{ color: "var(--color-muted)" }}>
            {config.showGroundingBadge
              ? "Grounding is the primary reliability signal. Strong = many genre books or ≥1 by this author. Model self-confidence shown separately as a secondary note."
              : "Low-confidence estimates — the rated nonfiction library is still small. Treat these as rough until it grows."}
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

          {kept.map((r, i) => (
            <ScoredCard
              key={r.title}
              result={r}
              rank={i + 1}
              primaryScore={config.primaryScore(r)}
              showGroundingBadge={config.showGroundingBadge}
              categoryOrder={r.category_order?.length ? r.category_order : categoryOrder}
              onRemove={() => removeBook(r.title)}
              saveMsg={saveResults[r.title]}
              refining={refiningTitles.has(r.title)}
              onRefine={config.refine ? () => refineOne(r.title) : undefined}
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
            <div className="flex items-center gap-3 pt-2 flex-wrap">
              <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                {savable.length > 0
                  ? `${savable.length} book${savable.length > 1 ? "s" : ""} will be saved to your ${config.savedNoun} — remove any you don't want with ✕`
                  : kept.length > 0
                    ? "All saved."
                    : "Every book removed — nothing to save."}
                {nRemoved > 0 && ` · ${nRemoved} removed`}
              </p>
              {nRemoved > 0 && (
                <button
                  onClick={restoreAll}
                  className="text-xs underline"
                  style={{ color: "var(--color-sage)" }}
                >
                  Restore {nRemoved}
                </button>
              )}
              {savable.length > 0 && (
                <SageButton onClick={handleSave} disabled={saving}>
                  {saving ? "Saving…" : `Save ${savable.length} to ${config.saveButtonNoun}`}
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
  primaryScore,
  showGroundingBadge,
  categoryOrder,
  onRemove,
  saveMsg,
  refining,
  onRefine,
}: {
  result: ScoredCandidate;
  rank: number;
  /** The value shown in the badge + used for ranking: WA (fiction) or Total
   *  Average (nonfiction). */
  primaryScore: number;
  /** Fiction shows the grounding badge; nonfiction shows the honest low-confidence
   *  note instead (no grounding/interval signal is available for it). */
  showGroundingBadge: boolean;
  categoryOrder: string[];
  onRemove: () => void;
  saveMsg?: string;
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
          {primaryScore.toFixed(2)}
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
        {/* Remove from the save set (opt-out): everything saves unless dropped here */}
        {!saveMsg && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="flex-shrink-0 text-base leading-none px-1.5 py-0.5 rounded-md"
            style={{ color: "var(--color-faint)" }}
            title="Remove — don't save this book"
            aria-label={`Remove ${result.title}`}
          >
            ✕
          </button>
        )}
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

          {/* PRIMARY reliability: the grounding badge (fiction) or the honest
              low-confidence note (nonfiction — no grounding/interval available). */}
          {showGroundingBadge ? (
            <GroundingBadge nGenre={result.n_genre} nAuthor={result.n_author} />
          ) : (
            <InfoBox
              message={
                `Low confidence — only ${result.total} nonfiction book` +
                `${result.total === 1 ? "" : "s"} rated, so this leans on priors. ` +
                `Treat as a rough estimate until the library grows.`
              }
            />
          )}

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
            Model self-confidence: {result.conf}
            {showGroundingBadge && " — less reliable than the grounding signal above."}
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

      {/* Save result row — only appears once this book has been saved (opt-out model:
          books save via the global button; the ✕ in the header removes unwanted ones). */}
      {saveMsg && (
        <div
          className="px-5 py-2"
          style={{
            borderTop: "1px solid var(--color-rule)",
            background: "var(--color-surface)",
          }}
        >
          <p
            className="text-xs"
            style={{ color: saveMsg.startsWith("Error") ? "#92400E" : "var(--color-sage)" }}
          >
            {saveMsg.startsWith("Error") ? saveMsg : `✓ ${saveMsg}`}
          </p>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   PER-KIND CONFIG — fiction and nonfiction share ONE flow component (PredictFlow);
   only these config objects differ. Fiction reproduces the original behaviour
   exactly (WA primary, grounded eager-refine, conformal interval when present).
   Nonfiction ranks by Total Average, has no grounded-refine and no residual
   interval, and shows the honest low-confidence note instead of a grounding badge.
   ═══════════════════════════════════════════════════════════════════════════ */

interface PredictFlowConfig {
  categoryOrder: string[];
  /** Badge label + heading noun: "WA" (fiction) or "Total Average" (nonfiction). */
  primaryLabel: string;
  /** The score a card leads with and the ranked list sorts by. */
  primaryScore: (r: ScoredCandidate) => number;
  /** Fiction shows the grounding badge; nonfiction shows the low-confidence note. */
  showGroundingBadge: boolean;
  requestTitle: string;
  requestHelp: string;
  placeholder: string;
  countHint: string;
  /** Per-new-candidate cost phrase in the candidate-table footer. */
  newCostHint: string;
  savedNoun: string;       // "…will be saved to your {savedNoun}"
  saveButtonNoun: string;  // "Save N to {saveButtonNoun}"
  discover: (request: string) => Promise<{
    candidates: Candidate[]; request: string; note?: string; sources?: string[];
  }>;
  score: (c: Candidate) => Promise<ScoredCandidate>;
  save: (r: ScoredCandidate) => Promise<{ message?: string }>;
  /** Grounded (hybrid) re-score. Fiction only — when omitted the eager pass, the
   *  per-card Refine button, and the refine banner all disable themselves. */
  refine?: (r: ScoredCandidate) => Promise<ScoredCandidate>;
}

/** Adapt a nonfiction prediction into the fiction-shaped ScoredCandidate the shared
 *  card consumes. Nonfiction carries no conformal interval and no hybrid upgrade,
 *  so those fields stay absent and the card degrades gracefully (no interval row,
 *  no Refine affordance). */
function nfToScored(p: NonfictionPrediction): ScoredCandidate {
  return {
    title: p.title, author: p.author, genre: p.genre,
    wa: p.wa, total_average: p.total_average,
    rank: p.rank, total: p.total,
    n_genre: p.n_genre ?? 0, n_author: p.n_author ?? 0,
    conf: p.confidence, from_cache: p.from_cache ?? false,
    words: p.words ?? null, series: p.series ?? "", series_number: p.series_number ?? null,
    blurb: p.blurb ?? "", keywords: p.keywords ?? "",
    components: p.components, category_order: p.category_order,
    genre_auto_detected: p.genre_auto_detected ?? false,
    sourcing: p.sourcing ?? "memory",
    hybrid_available: p.hybrid_available ?? false,
  };
}

function makeFictionConfig(categoryOrder: string[]): PredictFlowConfig {
  return {
    categoryOrder,
    primaryLabel: "WA",
    primaryScore: (r) => r.wa,
    showGroundingBadge: true,
    requestTitle: "What are you in the mood for?",
    requestHelp:
      "Ask in plain language. The LLM proposes candidates — avoiding what you've " +
      "already read — then your engine scores and ranks each one.",
    placeholder:
      "e.g. recommend 5 epic fantasy books · something like Toll the Hounds " +
      "but in a different genre · underrated sci-fi from the 2010s",
    countHint:
      "State how many you want in your request (e.g. “the 5 main books of …”, “a few " +
      "cozy mysteries”) — or name a single book to predict it directly.",
    newCostHint: "~1¢ and a few seconds each",
    savedNoun: "recommendations (TBR)",
    saveButtonNoun: "recommendations",
    discover: (request) => discoverCandidates(request),
    score: (c) => predictResearch(c.title, c.author, c.genre ?? undefined),
    refine: (r) => predictResearch(r.title, r.author, r.genre, true),
    save: (r) =>
      saveRecommendation({
        title: r.title, genre: r.genre, author: r.author,
        scores: flattenScores(r.components),
        words: r.words ?? undefined,
        series: r.series || undefined,
        series_number: r.series_number ?? undefined,
        blurb: r.blurb || undefined,
        keywords: r.keywords || undefined,
      }),
  };
}

function makeNonfictionConfig(categoryOrder: string[]): PredictFlowConfig {
  return {
    categoryOrder,
    primaryLabel: "Total Average",
    primaryScore: (r) => r.total_average ?? 0,
    showGroundingBadge: false,
    requestTitle: "What are you in the mood for?",
    requestHelp:
      "Ask in plain language. The LLM proposes real nonfiction books — avoiding what " +
      "you've already read — then your engine scores and ranks each one.",
    placeholder:
      "e.g. 5 books on behavioral economics · something like Sapiens but on economics · " +
      "underrated popular science from the 2010s",
    countHint:
      "State how many you want in your request (e.g. “5 books on stoicism”, “a few " +
      "history books”) — or name a single book to predict it directly.",
    newCostHint: "one Opus call each",
    savedNoun: "nonfiction TBR",
    saveButtonNoun: "nonfiction TBR",
    discover: async (request) => {
      const r = await discoverNonfictionCandidates(request);
      return {
        candidates: r.candidates.map((c) => ({
          title: c.title,
          author: c.author,
          genre: c.genre ?? "Nonfiction",
          cached: c.cached ?? false,
          series: c.series ?? null,
          series_number: c.series_number ?? null,
          requested: c.requested ?? false,
        })),
        request: r.request,
        note: r.note,
        sources: r.sources ?? [],
      };
    },
    score: async (c) =>
      nfToScored(await predictNonfiction(c.title, c.author, c.genre ?? undefined)),
    // no refine — nonfiction has no grounded-refine path
    save: (r) =>
      saveNonfictionRecommendation({
        title: r.title, author: r.author, genre: r.genre,
        scores: flattenScores(r.components),
        words: r.words ?? undefined,
        series: r.series || undefined,
        series_number: r.series_number ?? undefined,
        blurb: r.blurb || undefined,
        keywords: r.keywords || undefined,
      }),
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
   ROOT PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════════════════ */

export default function PredictClient({
  categoryOrder,
  nonfictionCategoryOrder,
}: {
  categoryOrder: string[];
  nonfictionCategoryOrder: string[];
}) {
  const [kind, setKind] = useState<BookKind>("fiction");
  const fictionConfig = useMemo(() => makeFictionConfig(categoryOrder), [categoryOrder]);
  const nonfictionConfig = useMemo(
    () => makeNonfictionConfig(nonfictionCategoryOrder),
    [nonfictionCategoryOrder],
  );
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
            ? "Discover nonfiction books — or name a single book — then let your engine score and rank them."
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

      {/* key={kind} remounts the flow when the toggle flips, so fiction results
          never leak into the nonfiction view (and vice-versa). */}
      <PredictFlow key={kind} config={kind === "fiction" ? fictionConfig : nonfictionConfig} />
    </div>
  );
}
