"use client";

import { useMemo, useState } from "react";
import { usePredictJobs, isRunBusy, EAGER_REFINE_K } from "@/lib/predict-jobs";
import { PREDICT_RUNNERS } from "@/lib/predict-runners";
import type { ResearchResult, ScoredCandidate, Candidate, BookKind } from "@/lib/types";
import { PredictNotifyToggle, PredictNotifyPrompt } from "@/components/PredictJobStatus";
import { SortableTable } from "@/components/SortableTable";
import type { ColDef } from "@/components/SortableTable";
import { ProgressBar } from "@/components/ProgressBar";
import { SkeletonCardList } from "@/components/Skeleton";

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

/* ── Stop control ────────────────────────────────────────────────────────────
   Quiet on purpose: stopping should be findable but never look like the primary
   action next to a bar that is making progress. Reuses the existing rule/muted
   tokens rather than introducing a destructive colour — nothing is destroyed by
   pressing it, since everything already scored is kept. */
function StopButton({
  onClick,
  label = "Stop",
  className = "",
}: {
  onClick: () => void;
  label?: string;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2.5 py-1 rounded-md transition-colors ${className}`}
      style={{
        background: "var(--color-surface-2)",
        color: "var(--color-muted)",
        border: "1px solid var(--color-rule)",
      }}
      title="Stop making further calls. Anything already done is kept; a call already in flight may still finish on the server."
    >
      {label}
    </button>
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
  const { kind, categoryOrder } = config;
  const jobs = usePredictJobs();
  const run = jobs.runs[kind];

  // Every piece of run state and every async driver lives in the provider (see
  // lib/predict-jobs.tsx), which is mounted above the router in the root layout.
  // That is the whole point: this component can unmount the instant the reader
  // clicks another tab and the run carries on regardless. What is left here is
  // presentation — plus the derived sets below, which are cheap to recompute and
  // belong with the view that renders them.
  const {
    request, requestLabel, candidates, genNote, genSources, genLoading, genError,
    scored, scoringIdx, scoringDone, saveResults, saving, saveProgress,
    repredictErrors, interrupted, cancelled,
  } = run;
  // One run per kind: the provider refuses to start a second while this one is
  // live (an abandoned loop would write into the new run), so the buttons that
  // start work say why instead of silently no-op-ing.
  const busy = isRunBusy(run);
  const refiningTitles = useMemo(() => new Set(run.refining), [run.refining]);
  const repredictingTitles = useMemo(() => new Set(run.repredicting), [run.repredicting]);
  const removed = useMemo(() => new Set(run.removed), [run.removed]);

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
  // Grounding progress across the whole kept set — the denominator for the
  // refine bar. A book already grounded when it was scored counts as done.
  const groundable = kept.filter((r) => r.hybrid_available || r.sourcing === "hybrid").length;
  const grounded = kept.filter((r) => r.sourcing === "hybrid").length;

  return (
    <div className="space-y-6">
      {/* A run that was still in flight when the page reloaded. The scores
          already in hand were restored from the tab's snapshot; anything mid-call
          died with the document. Say so plainly rather than presenting a
          half-finished run as a finished one. */}
      {interrupted && (
        <div
          className="rounded-lg px-4 py-3 text-sm flex items-start gap-3"
          style={{ background: "#FFFBEB", border: "1px solid #FCD34D", color: "#92400E" }}
        >
          <span className="flex-1">{interrupted}</span>
          <button
            onClick={() => jobs.dismissInterrupted(kind)}
            aria-label="Dismiss"
            className="flex-shrink-0 text-sm leading-none"
          >
            ✕
          </button>
        </div>
      )}

      {/* The reader pressed Stop. Sage, not amber: a partial run is the outcome
          they asked for, not something that went wrong. */}
      {cancelled && (
        <div
          className="rounded-lg px-4 py-3 text-sm flex items-start gap-3"
          style={{
            background: "var(--color-sage-light)",
            border: "1px solid var(--color-sage)",
            color: "var(--color-sage)",
          }}
        >
          <span className="flex-1">{cancelled}</span>
          <button
            onClick={() => jobs.dismissCancelled(kind)}
            aria-label="Dismiss"
            className="flex-shrink-0 text-sm leading-none"
          >
            ✕
          </button>
        </div>
      )}

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
          onChange={(e) => jobs.setRequest(kind, e.target.value)}
          placeholder={config.placeholder}
        />
        <p className="text-xs mt-2 mb-3" style={{ color: "var(--color-faint)" }}>
          {config.countHint}
        </p>
        <div className="flex items-center gap-4 mt-3">
          <SageButton
            onClick={() => jobs.generate(kind)}
            disabled={busy || !request.trim()}
          >
            {genLoading ? "Generating candidates…" : "Generate candidates"}
          </SageButton>
          {busy && !genLoading && (
            <span className="text-xs" style={{ color: "var(--color-faint)" }}>
              Finish or leave the current run first.
            </span>
          )}
          {/* Opt in to the OS-level notification. It fires only while this tab is
              in the BACKGROUND — on screen, the in-app banner already says it. */}
          <span className="ml-auto">
            <PredictNotifyToggle />
          </span>
        </div>
        {genLoading && (
          <>
            <ProgressBar
              className="mt-3"
              label="Asking the model for candidates…"
              hint="Usually a few seconds; longer when the request needs a web lookup for series order."
            />
            <StopButton onClick={() => jobs.cancelRun(kind)} className="mt-2" />
          </>
        )}
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
              <SageButton onClick={() => jobs.score(kind)} disabled={busy}>
                Confirm & score {candidates.length} candidate
                {candidates.length !== 1 ? "s" : ""}
              </SageButton>
            </div>
          )}

          {scoringIdx !== null && (
            <ProgressBar
              className="mt-4"
              value={scoringIdx + 1}
              max={candidates.length}
              label={`Scoring ${scoringIdx + 1} / ${candidates.length}: ${candidates[scoringIdx].title}`}
              hint={
                nNew > 0
                  ? `${nNew} of these have never been researched — those calls are the slow ones.`
                  : undefined
              }
            />
          )}

          {/* The run is owned by the root layout, not this page, so leaving is
              genuinely safe — say so, since nothing on screen would suggest it. */}
          {scoringIdx !== null && (
            <div className="mt-2">
              <StopButton
                onClick={() => jobs.cancelRun(kind)}
                label={`Stop — skip the remaining ${candidates.length - scoringIdx - 1}`}
              />
            </div>
          )}

          {scoringIdx !== null && (
            <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
              This keeps running if you switch tabs — the nav shows its progress and
              you&rsquo;ll get a note here when it finishes.{" "}
              <PredictNotifyPrompt />
            </p>
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
                  <StopButton onClick={() => jobs.cancelRun(kind)} />
                </span>
              )}
              {groundable > 0 && (
                // Determinate across the whole result set rather than per batch:
                // grounded ÷ groundable is stable whether the refine came from
                // the eager top-K pass, one card, or "Refine all".
                <ProgressBar
                  className="w-full order-last"
                  value={grounded}
                  max={groundable}
                  label={`${grounded} of ${groundable} grounded with reviews`}
                />
              )}
              {unrefined.length > 0 && (
                <button
                  onClick={() => jobs.refineRemaining(kind, kept)}
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
              onRemove={() => jobs.removeBook(kind, r.title)}
              saveMsg={saveResults[r.title]}
              refining={refiningTitles.has(r.title)}
              onRefine={config.hasRefine ? () => jobs.refineOne(kind, r.title) : undefined}
              repredicting={repredictingTitles.has(r.title)}
              onRepredict={config.hasRepredict ? () => jobs.repredictOne(kind, r.title) : undefined}
              repredictError={repredictErrors[r.title]}
            />
          ))}

          {/* The card currently being scored, as a placeholder — the results
              list grows one card at a time, so this shows where the next one
              lands instead of the list just sitting still. */}
          {scoringIdx !== null && <SkeletonCardList cards={1} />}

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
                  onClick={() => jobs.restoreAll(kind)}
                  className="text-xs underline"
                  style={{ color: "var(--color-sage)" }}
                >
                  Restore {nRemoved}
                </button>
              )}
              {savable.length > 0 && (
                <SageButton onClick={() => jobs.save(kind, savable)} disabled={saving}>
                  {saving ? "Saving…" : `Save ${savable.length} to ${config.saveButtonNoun}`}
                </SageButton>
              )}
            </div>
          )}

          {saving && saveProgress.total > 0 && (
            <>
              <ProgressBar
                value={saveProgress.done}
                max={saveProgress.total}
                label={`Saved ${saveProgress.done} / ${saveProgress.total}`}
                hint="Each save writes the prediction and generates its blurb."
              />
              <StopButton onClick={() => jobs.cancelRun(kind)} className="mt-2" />
            </>
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
  repredicting,
  onRepredict,
  repredictError,
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
  /** A no-cache re-score of this one book is in flight. */
  repredicting?: boolean;
  /** Fiction only — undefined hides the affordance entirely (nonfiction). */
  onRepredict?: () => void;
  repredictError?: string;
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

        {/* Re-predict: the no-cache fresh look. Lives in the header BESIDE Refine
            because that is where this page keeps its per-card actions — buried in
            the expanded panel it may as well not exist. Distinct from Refine:
            Refine is the one-time memory→grounded upgrade and disappears once a
            book is grounded; this works on any card, at any time. */}
        {onRepredict && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRepredict();
            }}
            disabled={repredicting}
            className="text-xs px-2 py-1 rounded-md flex-shrink-0 disabled:opacity-60"
            style={{
              border: "1px solid var(--color-rule)",
              color: repredicting ? "var(--color-sage)" : "var(--color-muted)",
              background: repredicting ? "var(--color-sage-light)" : "transparent",
            }}
            title="Ignore the cached research and predict this book again from scratch"
          >
            {repredicting ? "Re-predicting…" : "Re-predict"}
          </button>
        )}
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

      {/* Per-card work in flight. Sits under the header so it is visible whether
          or not the card is expanded — a refine or re-predict rewrites the score
          in the badge above it. */}
      {(refining || repredicting) && (
        <ProgressBar
          className="px-5 pb-3"
          label={repredicting ? "Predicting this book again from scratch…" : "Grounding with reader reviews…"}
        />
      )}

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

      {/* Re-predict failure — in the always-visible strip, not the collapsed
          panel, so a failed press can't report itself somewhere unseen. */}
      {repredictError && (
        <div
          className="px-5 py-2"
          style={{
            borderTop: "1px solid var(--color-rule)",
            background: "var(--color-surface)",
          }}
        >
          <p className="text-xs" style={{ color: "#92400E" }}>{repredictError}</p>
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

   This is now PRESENTATION ONLY. The calls a run actually makes live in
   lib/predict-runners.ts, keyed by the same `kind`, because the background job
   provider has to drive a run with no page mounted and therefore no config in
   hand. Anything here that describes BEHAVIOUR (primaryScore, whether a refine
   or re-predict path exists) is read off that runner rather than restated, so
   the two can't drift.
   ═══════════════════════════════════════════════════════════════════════════ */

interface PredictFlowConfig {
  /** Selects both the runner and this kind's slice of the job provider's state. */
  kind: BookKind;
  categoryOrder: string[];
  /** Badge label + heading noun: "WA" (fiction) or "Total Average" (nonfiction). */
  primaryLabel: string;
  /** The score a card leads with and the ranked list sorts by. Read off the
   *  runner — the eager-refine pass ranks by the same function. */
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
  /** Whether this kind has a grounded-refine path at all (fiction only). When
   *  false the refine banner and every per-card Refine affordance disappear. */
  hasRefine: boolean;
  /** Whether this kind offers the per-card no-cache Re-predict (fiction only —
   *  nonfiction's lives on its read-queue, where it can persist the result). */
  hasRepredict: boolean;
}

function makeFictionConfig(categoryOrder: string[]): PredictFlowConfig {
  return {
    kind: "fiction",
    categoryOrder,
    primaryLabel: "WA",
    primaryScore: PREDICT_RUNNERS.fiction.primaryScore,
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
    hasRefine: !!PREDICT_RUNNERS.fiction.refine,
    hasRepredict: !!PREDICT_RUNNERS.fiction.repredict,
  };
}

function makeNonfictionConfig(categoryOrder: string[]): PredictFlowConfig {
  return {
    kind: "nonfiction",
    categoryOrder,
    primaryLabel: "Total Average",
    primaryScore: PREDICT_RUNNERS.nonfiction.primaryScore,
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
    hasRefine: !!PREDICT_RUNNERS.nonfiction.refine,
    hasRepredict: !!PREDICT_RUNNERS.nonfiction.repredict,
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
  // The toggle's position lives in the job provider, not in local state: the
  // finish banner opens this page on whichever kind actually finished, and the
  // choice survives navigating away mid-run like everything else does.
  const { activeKind: kind, setActiveKind: setKind } = usePredictJobs();
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

      {/* key={kind} remounts the flow when the toggle flips. The two kinds' RUNS
          can no longer leak into each other regardless (the provider keys them
          separately), but this still resets the view-local card open/closed
          state, which should not carry across the toggle. */}
      <PredictFlow key={kind} config={kind === "fiction" ? fictionConfig : nonfictionConfig} />
    </div>
  );
}
