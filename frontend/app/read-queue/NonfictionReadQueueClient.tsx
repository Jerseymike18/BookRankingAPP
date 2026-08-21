"use client";

import React, { useState, useMemo, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { NonfictionReadQueueResponse, NonfictionRecommendation } from "@/lib/types";
import { saveNonfictionQueue, deleteNonfictionRecommendation } from "@/lib/api";
import { usePredictJobs, queueRepredictKey } from "@/lib/predict-jobs";
import { PredictNotifyPrompt } from "@/components/PredictJobStatus";
import { seriesLabel, componentLabel } from "@/lib/format";
import { useReadOnly } from "@/lib/readonly-context";
import { ProgressBar } from "@/components/ProgressBar";

/* ── Nonfiction schema: 12 components across 5 categories (2026 redesign; mirrors
 *    nonfiction_engine.NONFICTION_CATEGORY_ORDER + the DB component→category map). ── */
const NF_CATEGORIES: Record<string, string[]> = {
  Substance:  ["Informativeness", "Accuracy", "Originality"],
  Reasoning:  ["Argumentation", "Evidence"],
  Exposition: ["Clarity", "Structure"],
  Aesthetics: ["Prose", "Voice"],
  Impact:     ["Insights", "Thought-Provokingness", "Entertainment"],
};
const NF_CAT_ORDER = ["Substance", "Reasoning", "Exposition", "Aesthetics", "Impact"] as const;
const NF_CAT_ABBR: Record<string, string> = {
  Substance: "Subst", Reasoning: "Reason", Exposition: "Expos",
  Aesthetics: "Aes", Impact: "Impact",
};

/* ── Mood engine: 6 nonfiction moods, each a subset of the 12 components.
 *    Parallels the fiction Read-Queue mood engine (ReadQueueClient MOOD_COMPONENTS),
 *    but mapped to nonfiction's rubric. Dialing a mood up re-ranks the to-read list. ── */
const NF_MOOD_COMPONENTS: Record<string, string[]> = {
  Informative:         ["Informativeness", "Accuracy", "Evidence"],
  Rigorous:            ["Argumentation", "Evidence", "Accuracy"],
  "Thought-Provoking": ["Insights", "Thought-Provokingness", "Originality"],
  Accessible:          ["Clarity", "Structure"],
  "Well-Written":      ["Prose", "Voice"],
  Entertaining:        ["Entertainment", "Voice"],
};
const NF_MOOD_NAMES = Object.keys(NF_MOOD_COMPONENTS);

// Destructive-action red — the same value the fiction Read-Queue page uses; there
// is no design token for it (see globals.css). Kept identical for consistency.
const DANGER = "#c0392b";

/* ── Helpers ──────────────────────────────────────────────────────────── */
function formatWords(words: number | null): string | null {
  if (!words) return null;
  if (words >= 1_000_000) return `${(words / 1_000_000).toFixed(1)}M`;
  if (words >= 1_000) return `${Math.round(words / 1_000)}K`;
  return `${words}`;
}

/** The directional prediction range "6.2–9.5", or null when no interval is present. */
function formatInterval(rec: NonfictionRecommendation): string | null {
  if (rec.wa_low == null || rec.wa_high == null) return null;
  return `${rec.wa_low.toFixed(1)}–${rec.wa_high.toFixed(1)}`;
}

/** Weighted mean of a book's component scores over the active mood components
 *  (weights from the mood sliders). Null when no mood is dialed up. Mirrors the
 *  fiction moodScoreFor. */
function moodScoreFor(rec: NonfictionRecommendation, active: Record<string, number>): number | null {
  let num = 0;
  let den = 0;
  for (const [comp, wt] of Object.entries(active)) {
    const v = rec.components[comp];
    if (v !== null && v !== undefined && !Number.isNaN(v)) {
      num += v * wt;
      den += wt;
    }
  }
  return den > 0 ? num / den : null;
}

/* ── Mood control (±, 0–5) — mirrors the fiction MoodInput ─────────────── */
function MoodInput({ name, value, onChange }: { name: string; value: number; onChange: (v: number) => void }) {
  const comps = NF_MOOD_COMPONENTS[name];
  const active = value > 0;
  return (
    <div
      className="rounded-xl p-3 flex flex-col gap-2"
      style={{
        background: active ? "var(--color-sage-light)" : "var(--color-surface)",
        border: `1px solid ${active ? "var(--color-sage)" : "var(--color-rule)"}`,
        transition: "background 150ms, border-color 150ms",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold leading-tight" style={{ color: active ? "var(--color-sage)" : "var(--color-ink)", fontFamily: "var(--font-display)" }}>
          {name}
        </span>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={() => onChange(Math.max(0, value - 1))}
            disabled={value === 0}
            className="w-6 h-6 rounded flex items-center justify-center text-sm font-bold leading-none disabled:opacity-30"
            style={{ background: "var(--color-surface-2)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}
            aria-label={`Decrease ${name}`}
          >
            −
          </button>
          <span className="w-5 text-center text-sm font-bold tabular-nums" style={{ color: active ? "var(--color-sage)" : "var(--color-muted)", fontFamily: "var(--font-display)" }}>
            {value}
          </span>
          <button
            onClick={() => onChange(Math.min(5, value + 1))}
            disabled={value === 5}
            className="w-6 h-6 rounded flex items-center justify-center text-sm font-bold leading-none disabled:opacity-30"
            style={{ background: "var(--color-surface-2)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}
            aria-label={`Increase ${name}`}
          >
            +
          </button>
        </div>
      </div>
      <p className="text-xs leading-tight" style={{ color: "var(--color-muted)" }}>
        {comps.map((c) => componentLabel(c, "nonfiction")).join(" · ")}
      </p>
    </div>
  );
}

/* ── Component scores (grouped by the 3 nonfiction categories) ─────────── */
function ComponentScores({ components }: { components: Record<string, number | null> }) {
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
        Component Scores
      </p>
      {NF_CAT_ORDER.map((cat) => {
        const comps = NF_CATEGORIES[cat];
        const hasAny = comps.some((c) => components[c] !== null && components[c] !== undefined);
        if (!hasAny) return null;
        return (
          <div key={cat}>
            <p className="text-xs uppercase tracking-wider mb-1.5" style={{ color: "var(--color-faint)" }}>
              {cat}
            </p>
            <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(5rem, 1fr))" }}>
              {comps.map((comp) => {
                const v = components[comp];
                return (
                  <div key={comp} className="comp-tile">
                    <span className="comp-label">{componentLabel(comp, "nonfiction")}</span>
                    <span className="comp-value">{v !== null && v !== undefined ? v.toFixed(1) : "—"}</span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Sort types ───────────────────────────────────────────────────────── */
type NfSortField = "mood" | "wa" | "upside" | "Substance" | "Reasoning"
  | "Exposition" | "Aesthetics" | "Impact";
type SortDir = "desc" | "asc";

function sortValue(rec: NonfictionRecommendation, moodScore: number | null, field: NfSortField): number {
  if (field === "mood") return moodScore ?? -Infinity;
  if (field === "wa") return rec.wa ?? -Infinity;
  // Upside = a realistic good outcome (~76th pct), not the range ceiling; falls
  // back to the point WA. Mirrors the fiction read-queue upside sort.
  if (field === "upside") return rec.upside ?? rec.wa ?? -Infinity;
  return (rec.category_avgs ?? {})[field] ?? 0;
}

function SortHeader({
  field, label, active, dir, onClick,
}: {
  field: NfSortField;
  label: string;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
}) {
  return (
    <th
      onClick={onClick}
      className="text-right text-xs font-semibold uppercase tracking-wider cursor-pointer select-none px-3 py-2 whitespace-nowrap"
      style={{
        color: active ? "var(--color-sage)" : "var(--color-muted)",
        background: active ? "var(--color-sage-light)" : "transparent",
        borderBottom: "1px solid var(--color-rule)",
      }}
    >
      {label}
      {active ? (dir === "desc" ? " ▼" : " ▲") : ""}
    </th>
  );
}

/* ── Expandable row panel ─────────────────────────────────────────────── */
function RecExpandedPanel({ rec, moodScore, onDelete }: { rec: NonfictionRecommendation; moodScore?: number | null; onDelete: () => void }) {
  const ro = useReadOnly();
  const router = useRouter();
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // ── Re-predict this one book ────────────────────────────────────────────
  // Gated behind a confirm step, unlike the fiction button: nonfiction has no
  // correction layer, so this ALWAYS re-researches and always costs an API call.
  // A one-click spend would be too easy to trigger by accident.
  // The CONFIRM step stays local — it is a question to this card, not a job.
  // It matters more here than on the fiction side: a nonfiction re-predict has no
  // cached path and always spends one Opus call (a nonfiction book's scores don't
  // depend on the library, so re-running the cached path would return a
  // byte-identical vector forever). Backgrounding the work must not make it
  // easier to fire by accident.
  const [repredictConfirm, setRepredictConfirm] = useState(false);

  // The work itself lives in the root-layout provider, so it survives this card
  // collapsing or the reader leaving the page — and it always makes a live web
  // call here, so it is the slow one by construction.
  const jobs = usePredictJobs();
  const repredictJob = jobs.queueRepredicts[queueRepredictKey("nonfiction", rec.title)];
  const repredicting = repredictJob?.status === "running";
  const repredictReport = repredictJob?.status === "done" ? repredictJob.report : null;
  const repredictError = repredictJob?.status === "error" ? repredictJob.error : null;
  // A cancel is not a failure — but for THIS endpoint it is not a clean nothing
  // either, because it persists: the server may have finished and written the row
  // before the browser stopped listening. The note says so, and the refresh below
  // treats a cancel exactly like a completion so the row on screen is whatever
  // actually landed.
  const repredictCancelled =
    repredictJob?.status === "cancelled" ? repredictJob.error : null;

  function handleRepredict() {
    setRepredictConfirm(false);
    jobs.startQueueRepredict("nonfiction", rec.title);
  }

  // Refresh the route once, when a finished job actually rewrote the stored row.
  // Keyed on the job's settle timestamp so remounting this card cannot fire it
  // again.
  // Seeded from the job as it stands AT MOUNT: a job that finished before this
  // card instance existed is already reflected in the server data this render
  // came from, so it must not trigger another refresh. Only a job that settles
  // while this instance is watching does.
  const refreshedAt = useRef<number | null>(
    repredictJob && repredictJob.status !== "running" ? repredictJob.at : null,
  );
  useEffect(() => {
    if (!repredictJob) return;
    if (repredictJob.status === "done") {
      if (!repredictJob.report?.written) return;
    } else if (repredictJob.status !== "cancelled") {
      return;
    }
    if (refreshedAt.current === repredictJob.at) return;
    refreshedAt.current = repredictJob.at;
    router.refresh();
  }, [repredictJob, router]);

  return (
    <div className="px-5 py-4 space-y-4" style={{ background: "var(--color-surface-2)", borderTop: "1px solid var(--color-rule)" }}>
      {/* Stats row */}
      <div className="flex flex-wrap gap-4 text-sm" style={{ color: "var(--color-muted)" }}>
        {rec.wa != null && (
          <span>Predicted WA: <strong style={{ color: "var(--color-ink)" }}>{rec.wa.toFixed(2)}</strong></span>
        )}
        {formatInterval(rec) && (
          <span>
            Range: <strong style={{ color: "var(--color-ink)" }}>{formatInterval(rec)}</strong>
            {rec.interval_label ? <span style={{ color: "var(--color-faint)" }}> ({rec.interval_label})</span> : null}
          </span>
        )}
        {rec.upside != null && (
          <span>Upside: <strong style={{ color: "var(--color-ink)" }}>{rec.upside.toFixed(2)}</strong></span>
        )}
        {moodScore != null && (
          <span>Mood score: <strong style={{ color: "var(--color-sage)" }}>{moodScore.toFixed(2)}</strong></span>
        )}
        {rec.predicted_rank != null && (
          <span>Predicted rank: <strong style={{ color: "var(--color-ink)" }}>#{rec.predicted_rank}</strong></span>
        )}
        <span className="genre-chip">{rec.genre}</span>
        {rec.words && (
          <span>Words: <strong style={{ color: "var(--color-ink)" }}>{rec.words.toLocaleString()}</strong></span>
        )}
        {rec.series && (
          <span style={{ color: "var(--color-faint)" }}>Series: {seriesLabel(rec.series, rec.series_number)}</span>
        )}
      </div>

      {/* Blurb */}
      {rec.blurb && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Blurb</p>
          <p className="text-sm leading-relaxed" style={{ color: "var(--color-ink)" }}>{rec.blurb}</p>
        </div>
      )}

      {/* Keywords */}
      {rec.keywords && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: "var(--color-muted)" }}>Keywords</p>
          <div className="flex flex-wrap gap-1.5">
            {rec.keywords.split(",").map((kw) => kw.trim()).filter(Boolean).map((kw) => (
              <span
                key={kw}
                className="text-xs px-2 py-0.5 rounded-full"
                style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
              >
                {kw}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Component scores */}
      <ComponentScores components={rec.components} />

      {/* Re-predict + remove (mutations — hidden on a read-only deploy) */}
      {!ro && (
        <div className="pt-2 border-t space-y-3" style={{ borderColor: "var(--color-rule)" }}>
        <div className="flex items-center gap-3">
          {/* Re-predict: two-step, because it always spends an API call */}
          {!deleteConfirm && (repredictConfirm ? (
            <>
              <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                Re-research this book? Costs one API call.
              </span>
              <button
                onClick={handleRepredict}
                className="text-xs px-3 py-1.5 rounded-lg font-semibold"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                Yes, re-predict
              </button>
              <button
                onClick={() => setRepredictConfirm(false)}
                className="text-xs px-3 py-1.5 rounded-lg"
                style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              onClick={() => setRepredictConfirm(true)}
              disabled={repredicting}
              title="Re-research this book and rewrite its prediction (spends one API call)"
              className="text-xs px-3 py-1.5 rounded-lg transition-colors disabled:opacity-60"
              style={{
                background: repredicting ? "var(--color-sage-light)" : "var(--color-surface-2)",
                color: repredicting ? "var(--color-sage)" : "var(--color-muted)",
                border: "1px solid var(--color-rule)",
              }}
            >
              {repredicting ? "Re-predicting…" : "Re-predict"}
            </button>
          ))}

          {!repredictConfirm && (!deleteConfirm ? (
            <button
              onClick={() => setDeleteConfirm(true)}
              className="text-xs px-3 py-1.5 rounded-lg transition-colors"
              style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
            >
              Remove from TBR
            </button>
          ) : (
            <>
              <span className="text-xs" style={{ color: DANGER }}>Remove permanently?</span>
              <button
                onClick={async () => {
                  setIsDeleting(true);
                  setDeleteError(null);
                  try {
                    await deleteNonfictionRecommendation(rec.title);
                    onDelete();
                  } catch (e: unknown) {
                    setDeleteError(e instanceof Error ? e.message : "Delete failed.");
                    setDeleteConfirm(false);
                  } finally {
                    setIsDeleting(false);
                  }
                }}
                disabled={isDeleting}
                className="text-xs px-3 py-1.5 rounded-lg font-semibold disabled:opacity-40"
                style={{ background: DANGER, color: "#fff" }}
              >
                {isDeleting ? "Removing…" : "Yes, remove"}
              </button>
              <button
                onClick={() => setDeleteConfirm(false)}
                className="text-xs px-3 py-1.5 rounded-lg"
                style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
              >
                Cancel
              </button>
            </>
          ))}
          {deleteError && <span className="text-xs" style={{ color: DANGER }}>{deleteError}</span>}
        </div>

        {/* Re-prediction outcome. Unlike fiction, "no change" here means the model
            returned the same scores on a genuinely fresh look — not that the
            baseline sat still (nonfiction has no baseline to move). */}
        {repredicting && (
          <ProgressBar
            label="Re-researching this book…"
            hint="A forced fresh research call — this usually takes under a minute. You can collapse this card or leave the page — it keeps running, and you'll be told when it lands."
          />
        )}
        {repredicting && <PredictNotifyPrompt className="self-start" />}
        {repredicting && (
          <button
            onClick={() => jobs.cancelQueueRepredict("nonfiction", rec.title)}
            className="text-xs px-2.5 py-1 rounded-md self-start"
            style={{
              background: "var(--color-surface-2)",
              color: "var(--color-muted)",
              border: "1px solid var(--color-rule)",
            }}
            title="Stop waiting. The Opus call is already spent, and the server may already have saved the new prediction."
          >
            Stop
          </button>
        )}
        {repredictReport && !repredicting && (
          <div className="text-xs space-y-1" style={{ color: "var(--color-muted)" }}>
            {repredictReport.changed ? (
              <>
                <p>
                  Re-predicted:{" "}
                  <strong style={{ color: "var(--color-ink)" }}>
                    WA {repredictReport.old_wa?.toFixed(2) ?? "—"} → {repredictReport.new_wa.toFixed(2)}
                  </strong>
                  {repredictReport.d_wa != null && (
                    <span style={{ color: repredictReport.d_wa >= 0 ? "var(--color-sage)" : "#B45309" }}>
                      {" "}({repredictReport.d_wa >= 0 ? "+" : ""}{repredictReport.d_wa.toFixed(2)})
                    </span>
                  )}
                  {repredictReport.old_rank != null && (
                    <span style={{ color: "var(--color-faint)" }}>
                      {" "}· rank #{repredictReport.old_rank} → #{repredictReport.new_rank} of{" "}
                      {repredictReport.total}
                    </span>
                  )}
                </p>
                {repredictReport.drivers.length > 0 && (
                  <p style={{ color: "var(--color-faint)" }}>
                    Biggest moves:{" "}
                    {repredictReport.drivers
                      .filter((d) => Math.abs(d.delta) >= 0.01)
                      .map((d) => `${componentLabel(d.component, "nonfiction")} ${d.delta >= 0 ? "+" : ""}${d.delta.toFixed(2)}`)
                      .join(" · ") || "none"}
                  </p>
                )}
              </>
            ) : (
              <p>
                Re-researched — <strong style={{ color: "var(--color-ink)" }}>no change</strong>{" "}
                <span style={{ color: "var(--color-faint)" }}>
                  (still WA {repredictReport.new_wa.toFixed(2)}; a fresh look landed on the
                  same scores)
                </span>
              </p>
            )}
          </div>
        )}
        {repredictError && <p className="text-xs" style={{ color: DANGER }}>{repredictError}</p>}
        {repredictCancelled && (
          <p className="text-xs" style={{ color: "var(--color-muted)" }}>{repredictCancelled}</p>
        )}
        {/* The result now outlives this panel — it lives in the job provider, so
            collapsing the card or leaving the page no longer discards it. That is
            the point, but it also means the reader needs a way to put it away. */}
        {repredictJob && !repredicting && (
          <button
            onClick={() => jobs.clearQueueRepredict("nonfiction", rec.title)}
            className="text-xs underline self-start"
            style={{ color: "var(--color-faint)" }}
          >
            Dismiss
          </button>
        )}
        </div>
      )}
    </div>
  );
}

/* ── Filter inputs ────────────────────────────────────────────────────── */
function FilterSelect({ value, onChange, children }: { value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)", color: "var(--color-ink)", fontFamily: "var(--font-body)" }}
    >
      {children}
    </select>
  );
}

function FilterText({ placeholder, value, onChange }: { placeholder: string; value: string; onChange: (v: string) => void }) {
  return (
    <input
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 min-w-0"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)", color: "var(--color-ink)", fontFamily: "var(--font-body)" }}
    />
  );
}

/* ── Queue tab ────────────────────────────────────────────────────────── */
function QueueExpandedPanel({ rec }: { rec: NonfictionRecommendation }) {
  return (
    <div className="px-5 py-4 space-y-4" style={{ background: "var(--color-surface-2)", borderTop: "1px solid var(--color-rule)" }}>
      <div className="flex flex-wrap gap-4 text-sm" style={{ color: "var(--color-muted)" }}>
        {rec.wa != null && (
          <span>Predicted WA: <strong style={{ color: "var(--color-ink)" }}>{rec.wa.toFixed(2)}</strong></span>
        )}
        {formatInterval(rec) && (
          <span>Range: <strong style={{ color: "var(--color-ink)" }}>{formatInterval(rec)}</strong></span>
        )}
        {rec.predicted_rank != null && (
          <span>Predicted rank: <strong style={{ color: "var(--color-ink)" }}>#{rec.predicted_rank}</strong></span>
        )}
        <span className="genre-chip">{rec.genre}</span>
        {rec.words && (
          <span>Words: <strong style={{ color: "var(--color-ink)" }}>{rec.words.toLocaleString()}</strong></span>
        )}
        {rec.series && (
          <span style={{ color: "var(--color-faint)" }}>Series: {seriesLabel(rec.series, rec.series_number)}</span>
        )}
      </div>
      <ComponentScores components={rec.components} />
    </div>
  );
}

function QueueCard({
  title, rec, rank, isDragging, isOver, isExpanded,
  onDragStart, onDragOver, onDrop, onDragEnd, onRemove, onToggleExpand,
}: {
  title: string;
  rec: NonfictionRecommendation | undefined;
  rank: number;
  isDragging: boolean;
  isOver: boolean;
  isExpanded: boolean;
  onDragStart: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: () => void;
  onDragEnd: () => void;
  onRemove: () => void;
  onToggleExpand: () => void;
}) {
  const words = rec ? formatWords(rec.words) : null;
  return (
    <article
      className="book-card shadow-sm"
      style={{
        borderTop: "1px solid var(--color-rule)",
        borderRight: "1px solid var(--color-rule)",
        borderBottom: isExpanded ? "none" : "1px solid var(--color-rule)",
        borderLeft: `3px solid ${isOver || isExpanded ? "var(--color-sage)" : "var(--color-rule)"}`,
        opacity: isDragging ? 0.4 : 1,
        transition: "opacity 150ms, border-color 150ms",
        background: isOver ? "var(--color-sage-light)" : undefined,
      }}
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
    >
      <div className="px-4 py-3 flex items-center gap-3 select-none cursor-pointer" onClick={onToggleExpand}>
        {/* Drag handle */}
        <svg className="w-4 h-4 flex-shrink-0" style={{ color: "var(--color-faint)", cursor: "grab" }} fill="currentColor" viewBox="0 0 20 20" onClick={(e) => e.stopPropagation()}>
          <path d="M7 4a1 1 0 100-2 1 1 0 000 2zM7 8a1 1 0 100-2 1 1 0 000 2zM7 12a1 1 0 100-2 1 1 0 000 2zM7 16a1 1 0 100-2 1 1 0 000 2zM13 4a1 1 0 100-2 1 1 0 000 2zM13 8a1 1 0 100-2 1 1 0 000 2zM13 12a1 1 0 100-2 1 1 0 000 2zM13 16a1 1 0 100-2 1 1 0 000 2z" />
        </svg>

        {/* Rank badge */}
        <div className="wa-badge flex-shrink-0" style={{ background: rank === 1 ? "var(--color-sage)" : "var(--color-faint)", minWidth: "2.2rem" }}>
          #{rank}
        </div>

        {/* Title / author / series */}
        <div className="flex-1 min-w-0">
          <h3 className="font-display font-semibold text-base leading-tight truncate" style={{ color: "var(--color-ink)" }}>{title}</h3>
          {rec && (
            <p className="text-sm mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
              {rec.author}
              {rec.series ? <span style={{ color: "var(--color-faint)" }}> · {seriesLabel(rec.series, rec.series_number)}</span> : null}
            </p>
          )}
        </div>

        {/* Genre + words */}
        {rec && (
          <div className="hidden sm:flex flex-col items-end gap-1 flex-shrink-0">
            <span className="genre-chip">{rec.genre}</span>
            {words && <span className="text-xs" style={{ color: "var(--color-faint)" }}>{words} words</span>}
          </div>
        )}

        {/* Predicted WA inline (collapsed) */}
        {rec && !isExpanded && rec.wa != null && (
          <div className="hidden sm:flex flex-col items-end flex-shrink-0 ml-1">
            <span className="text-xs font-semibold tabular-nums" style={{ color: "var(--color-sage)" }}>{rec.wa.toFixed(2)}</span>
            <span className="text-xs" style={{ color: "var(--color-faint)" }}>pred WA</span>
          </div>
        )}

        {/* Chevron */}
        <svg className="w-4 h-4 flex-shrink-0 transition-transform" style={{ color: "var(--color-faint)", transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)" }} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>

        {/* Remove */}
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="flex-shrink-0 w-7 h-7 rounded flex items-center justify-center text-base font-bold leading-none transition-colors"
          style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
          aria-label={`Remove ${title}`}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = DANGER; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--color-muted)"; }}
        >
          ×
        </button>
      </div>

      {/* Expanded panel */}
      {isExpanded && (
        rec ? (
          <QueueExpandedPanel rec={rec} />
        ) : (
          <div className="px-5 py-4" style={{ background: "var(--color-surface-2)", borderTop: "1px solid var(--color-rule)" }}>
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>No prediction yet for this title.</p>
          </div>
        )
      )}
    </article>
  );
}

function QueueTab({ initialQueue, recommendations }: { initialQueue: string[]; recommendations: NonfictionRecommendation[] }) {
  const [items, setItems] = useState<string[]>(initialQueue);
  const [textMode, setTextMode] = useState(false);
  const [text, setText] = useState(() => initialQueue.join("\n"));
  const [addInput, setAddInput] = useState("");
  const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const savedRef = useRef<string[]>(initialQueue);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);
  const [expandedTitle, setExpandedTitle] = useState<string | null>(null);

  const recByTitle = useMemo(() => {
    const m = new Map<string, NonfictionRecommendation>();
    for (const r of recommendations) m.set(r.title, r);
    return m;
  }, [recommendations]);

  const isDirty = JSON.stringify(items) !== JSON.stringify(savedRef.current);

  async function handleSave(overrideTitles?: string[]) {
    setSaving(true);
    setStatus(null);
    try {
      const titles = overrideTitles ?? items;
      const res = await saveNonfictionQueue(titles);
      savedRef.current = titles;
      setItems(titles);
      setText(titles.join("\n"));
      setStatus({ ok: true, msg: res.message || `Queue updated (${titles.length} books).` });
    } catch (e: unknown) {
      setStatus({ ok: false, msg: e instanceof Error ? e.message : "Save failed." });
    } finally {
      setSaving(false);
    }
  }

  function handleTextSave() {
    const titles = text.split("\n").map((t) => t.trim()).filter(Boolean);
    handleSave(titles);
  }

  function handleRemove(idx: number) {
    setItems((prev) => prev.filter((_, i) => i !== idx));
    setStatus(null);
  }

  function handleAdd() {
    const t = addInput.trim();
    if (!t) return;
    setItems((prev) => (prev.includes(t) ? prev : [...prev, t]));
    setAddInput("");
    setStatus(null);
  }

  function handleDrop(idx: number) {
    if (dragIdx === null || dragIdx === idx) { setDragIdx(null); setDragOverIdx(null); return; }
    setItems((prev) => {
      const next = [...prev];
      const [moved] = next.splice(dragIdx, 1);
      next.splice(idx, 0, moved);
      return next;
    });
    setDragIdx(null);
    setDragOverIdx(null);
    setStatus(null);
  }

  function toggleTextMode() {
    if (!textMode) setText(items.join("\n"));
    setTextMode((m) => !m);
    setStatus(null);
  }

  if (textMode) {
    const lines = text.split("\n").filter((t) => t.trim()).length;
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>One title per line — top is next up.</p>
          <button onClick={toggleTextMode} className="text-xs px-3 py-1.5 rounded-lg" style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}>
            Card view
          </button>
        </div>
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setStatus(null); }}
          rows={18}
          className="w-full rounded-xl px-4 py-3 text-sm font-mono focus:outline-none focus:ring-2 resize-y"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)", color: "var(--color-ink)", lineHeight: "1.7" }}
          placeholder="Paste or type one book title per line…"
          spellCheck={false}
        />
        <div className="flex items-center gap-3">
          <button onClick={handleTextSave} disabled={saving} className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity" style={{ background: "var(--color-sage)", color: "#fff" }}>
            {saving ? "Saving…" : "Save queue"}
          </button>
          <span className="text-xs" style={{ color: "var(--color-faint)" }}>{lines} title{lines !== 1 ? "s" : ""}</span>
          {status && (
            <span className="text-sm" style={{ color: status.ok ? "var(--color-sage)" : DANGER }}>
              {status.ok ? "✓ " : "✗ "}{status.msg}
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          Drag to reorder — top is next up. Changes are saved with the button below.
        </p>
        <button onClick={toggleTextMode} className="text-xs px-3 py-1.5 rounded-lg flex-shrink-0" style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}>
          Edit as text
        </button>
      </div>

      <div className="space-y-2">
        {items.length === 0 && (
          <p className="text-sm text-center py-8" style={{ color: "var(--color-faint)" }}>Queue is empty — add a book below.</p>
        )}
        {items.map((title, i) => (
          <QueueCard
            key={title}
            title={title}
            rec={recByTitle.get(title)}
            rank={i + 1}
            isDragging={dragIdx === i}
            isOver={dragOverIdx === i}
            isExpanded={expandedTitle === title}
            onDragStart={() => setDragIdx(i)}
            onDragOver={(e) => { e.preventDefault(); setDragOverIdx(i); }}
            onDrop={() => handleDrop(i)}
            onDragEnd={() => { setDragIdx(null); setDragOverIdx(null); }}
            onRemove={() => handleRemove(i)}
            onToggleExpand={() => setExpandedTitle((t) => (t === title ? null : title))}
          />
        ))}
      </div>

      {/* Add book */}
      <div className="flex gap-2 pt-1">
        <input
          type="text"
          placeholder="Add a book title…"
          value={addInput}
          onChange={(e) => setAddInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleAdd(); }}
          className="flex-1 px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 min-w-0"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)", color: "var(--color-ink)", fontFamily: "var(--font-body)" }}
        />
        <button onClick={handleAdd} disabled={!addInput.trim()} className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity" style={{ background: "var(--color-surface-2)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}>
          Add
        </button>
      </div>

      {/* Save bar */}
      <div className="flex items-center gap-3 pt-1">
        <button onClick={() => handleSave()} disabled={saving || !isDirty} className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity" style={{ background: "var(--color-sage)", color: "#fff" }}>
          {saving ? "Saving…" : "Save queue"}
        </button>
        <span className="text-xs" style={{ color: "var(--color-faint)" }}>
          {items.length} title{items.length !== 1 ? "s" : ""}{isDirty ? " · unsaved changes" : ""}
        </span>
        {status && (
          <span className="text-sm" style={{ color: status.ok ? "var(--color-sage)" : DANGER }}>
            {status.ok ? "✓ " : "✗ "}{status.msg}
          </span>
        )}
      </div>
    </div>
  );
}

/* ── Main client ──────────────────────────────────────────────────────── */
export default function NonfictionReadQueueClient({
  data,
  initialQueue,
}: {
  data: NonfictionReadQueueResponse;
  initialQueue: string[];
}) {
  const ro = useReadOnly();
  const { recommendations } = data;
  const [deletedTitles, setDeletedTitles] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<"list" | "queue">("list");
  const [sortField, setSortField] = useState<NfSortField>("mood");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [expandedTitle, setExpandedTitle] = useState<string | null>(null);

  /* Mood weights (0–5 each). Dial a mood up and the to-read list re-ranks to match;
     all at 0 falls back to predicted-rank (WA) order. Mirrors the fiction read-queue. */
  const [moodWeights, setMoodWeights] = useState<Record<string, number>>(
    () => Object.fromEntries(NF_MOOD_NAMES.map((m) => [m, 0])),
  );
  const setMood = useCallback((name: string, v: number) => {
    setMoodWeights((prev) => ({ ...prev, [name]: v }));
  }, []);
  const resetMoods = useCallback(() => {
    setMoodWeights(Object.fromEntries(NF_MOOD_NAMES.map((m) => [m, 0])));
  }, []);
  // Aggregate the dialed moods down to per-component weights.
  const active = useMemo(() => {
    const impl: Record<string, number> = {};
    for (const [mood, comps] of Object.entries(NF_MOOD_COMPONENTS)) {
      const w = moodWeights[mood] ?? 0;
      if (w <= 0) continue;
      for (const c of comps) impl[c] = (impl[c] ?? 0) + w;
    }
    return impl;
  }, [moodWeights]);
  const hasMoods = Object.keys(active).length > 0;

  /* Filters */
  const [fTitle, setFTitle] = useState("");
  const [fGenre, setFGenre] = useState("All genres");
  const [fLength, setFLength] = useState("Any");
  const [fType, setFType] = useState("Any");
  const [fAuthor, setFAuthor] = useState("");
  const [fKeyword, setFKeyword] = useState("");

  const genreOptions = useMemo(
    () => Array.from(new Set(recommendations.map((r) => r.genre).filter(Boolean))).sort(),
    [recommendations],
  );

  const results = useMemo(() => {
    let list = recommendations.filter((r) => !deletedTitles.has(r.title));
    // Title lookup — matches the series name too (mirrors fiction).
    if (fTitle.trim()) {
      const q = fTitle.trim().toLowerCase();
      list = list.filter((r) => r.title.toLowerCase().includes(q) || r.series.toLowerCase().includes(q));
    }
    if (fGenre !== "All genres") list = list.filter((r) => r.genre === fGenre);
    if (fLength !== "Any") {
      if (fLength === "Short (<100K)") list = list.filter((r) => r.words !== null && r.words < 100_000);
      else if (fLength === "Medium (100–200K)") list = list.filter((r) => r.words !== null && r.words >= 100_000 && r.words <= 200_000);
      else list = list.filter((r) => r.words !== null && r.words > 200_000);
    }
    if (fType !== "Any") list = list.filter((r) => (fType === "Series" ? r.series.length > 0 : r.series.length === 0));
    if (fAuthor.trim()) { const q = fAuthor.trim().toLowerCase(); list = list.filter((r) => r.author.toLowerCase().includes(q)); }
    if (fKeyword.trim()) { const q = fKeyword.trim().toLowerCase(); list = list.filter((r) => r.keywords.toLowerCase().includes(q)); }
    // Attach a mood score (null when no mood is active) so sort + render can use it.
    return list.map((r) => ({ rec: r, moodScore: hasMoods ? moodScoreFor(r, active) : null }));
  }, [recommendations, deletedTitles, fTitle, fGenre, fLength, fType, fAuthor, fKeyword, hasMoods, active]);

  // A lookup miss only means "not on your to-read list" when nothing else filters.
  const otherFiltersActive =
    fGenre !== "All genres" || fLength !== "Any" || fType !== "Any" || !!fAuthor.trim() || !!fKeyword.trim();

  const sortedResults = useMemo(() => {
    const mult = sortDir === "desc" ? -1 : 1;
    return [...results].sort((a, b) => {
      // Mood sort with no moods dialed up falls back to predicted-rank (WA) order.
      if (sortField === "mood" && !hasMoods) {
        return (a.rec.predicted_rank ?? Infinity) - (b.rec.predicted_rank ?? Infinity);
      }
      return mult * (sortValue(a.rec, a.moodScore, sortField) - sortValue(b.rec, b.moodScore, sortField));
    });
  }, [results, sortField, sortDir, hasMoods]);

  function handleSortClick(field: NfSortField) {
    if (field === sortField) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortField(field); setSortDir("desc"); }
  }

  const remaining = recommendations.length - deletedTitles.size;

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Nonfiction Read Queue
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {remaining} book{remaining !== 1 ? "s" : ""} in your to-read list · {initialQueue.length} in your ordered queue
        </p>
      </div>

      {/* Tab bar — the Queue tab is an editor, hidden on a read-only deploy. */}
      <div className="flex gap-1 mb-6 p-1 rounded-xl w-fit" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
        {(ro ? (["list"] as const) : (["list", "queue"] as const)).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            style={{ background: tab === t ? "var(--color-sage)" : "transparent", color: tab === t ? "#fff" : "var(--color-muted)" }}
          >
            {t === "list" ? "To-Read" : "Queue"}
          </button>
        ))}
      </div>

      {!ro && tab === "queue" && <QueueTab initialQueue={initialQueue} recommendations={recommendations} />}

      {tab === "list" && recommendations.length === 0 ? (
        <div className="text-center py-16 rounded-xl" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            No nonfiction recommendations yet — research books on the Predict page to add them here.
          </p>
        </div>
      ) : tab === "list" ? (
        <>
          {/* Mood — dial a mood up to re-rank the to-read list (mirrors fiction). */}
          <section className="mb-6 rounded-xl p-5" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
            <div className="flex items-center justify-between mb-1">
              <h2 className="font-display font-semibold text-lg" style={{ color: "var(--color-ink)" }}>Mood</h2>
              {hasMoods && (
                <button onClick={resetMoods} className="text-xs px-2 py-1 rounded-lg" style={{ color: "var(--color-muted)", background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}>
                  Reset all
                </button>
              )}
            </div>
            <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
              Dial up the moods you want — the list re-ranks to match. All at 0 falls back to predicted-rank order.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {NF_MOOD_NAMES.map((name) => (
                <MoodInput key={name} name={name} value={moodWeights[name] ?? 0} onChange={(v) => setMood(name, v)} />
              ))}
            </div>
          </section>

          {/* Filters */}
          <section className="mb-6 rounded-xl p-5" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
            <h2 className="font-display font-semibold text-lg mb-4" style={{ color: "var(--color-ink)" }}>Filters</h2>
            <div className="flex flex-wrap gap-3">
              <FilterText placeholder="Title or series…" value={fTitle} onChange={setFTitle} />
              {genreOptions.length > 1 && (
                <FilterSelect value={fGenre} onChange={setFGenre}>
                  <option value="All genres">All genres</option>
                  {genreOptions.map((g) => <option key={g} value={g}>{g}</option>)}
                </FilterSelect>
              )}
              <FilterSelect value={fLength} onChange={setFLength}>
                <option value="Any">Any length</option>
                <option value="Short (<100K)">Short (&lt;100K)</option>
                <option value="Medium (100–200K)">Medium (100–200K)</option>
                <option value="Long (>200K)">Long (&gt;200K)</option>
              </FilterSelect>
              <FilterSelect value={fType} onChange={setFType}>
                <option value="Any">Any type</option>
                <option value="Series">Series</option>
                <option value="Standalone">Standalone</option>
              </FilterSelect>
              <FilterText placeholder="Author contains…" value={fAuthor} onChange={setFAuthor} />
              <FilterText placeholder="Keyword tag…" value={fKeyword} onChange={setFKeyword} />
            </div>
          </section>

          {/* Results */}
          <section>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <h2 className="font-display font-semibold text-lg" style={{ color: "var(--color-ink)" }}>Results</h2>
                <div className="px-3 py-1 rounded-full text-sm font-medium" style={{ background: "var(--color-sage-light)", color: "var(--color-sage)" }}>
                  {results.length}
                </div>
              </div>
              <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                click a column header to sort · click a row to expand ·{" "}
                <span title="A good outcome — roughly the 76th percentile, one you'd beat about 1 in 4 reads (not the range ceiling). Sorting by Upside surfaces under-rated picks.">Upside ≈ 76th-pct</span> ·{" "}
                <span title="Small-sample directional band from the nonfiction leave-one-out residuals — the same width for every book and NOT yet calibrated (n≈6).">range is directional</span>
              </span>
            </div>

            {results.length === 0 ? (
              <p className="text-center py-10 text-sm" style={{ color: "var(--color-muted)" }}>
                {fTitle.trim() && !otherFiltersActive
                  ? `Nothing matching “${fTitle.trim()}” is on your to-read list.`
                  : "No books match your filters."}
              </p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                  <thead>
                    <tr style={{ background: "var(--color-surface)" }}>
                      <th className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2" style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)", minWidth: "2rem" }}>#</th>
                      <th className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2" style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)", minWidth: "12rem" }}>Book</th>
                      {hasMoods && (
                        <SortHeader field="mood" label="Mood" active={sortField === "mood"} dir={sortDir} onClick={() => handleSortClick("mood")} />
                      )}
                      <SortHeader field="wa" label="Pred WA" active={sortField === "wa"} dir={sortDir} onClick={() => handleSortClick("wa")} />
                      <SortHeader field="upside" label="Upside" active={sortField === "upside"} dir={sortDir} onClick={() => handleSortClick("upside")} />
                      {NF_CAT_ORDER.map((cat) => (
                        <SortHeader key={cat} field={cat} label={NF_CAT_ABBR[cat]} active={sortField === cat} dir={sortDir} onClick={() => handleSortClick(cat)} />
                      ))}
                      <th className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2" style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)" }}>Genre</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedResults.map(({ rec, moodScore }, i) => {
                      const isExpanded = expandedTitle === rec.title;
                      const avgs = rec.category_avgs ?? {};
                      const colCount = 5 + NF_CAT_ORDER.length + (hasMoods ? 1 : 0);
                      return (
                        <React.Fragment key={rec.title}>
                          <tr
                            onClick={() => setExpandedTitle(isExpanded ? null : rec.title)}
                            className="cursor-pointer"
                            style={{ borderBottom: isExpanded ? "none" : "1px solid var(--color-rule)", transition: "background 0.1s" }}
                          >
                            <td className="px-3 py-3 font-display italic text-sm text-right" style={{ color: "var(--color-faint)", minWidth: "2.5rem" }}>{i + 1}</td>
                            <td className="px-3 py-3" style={{ minWidth: "12rem" }}>
                              <div className="font-display font-semibold text-sm leading-tight" style={{ color: "var(--color-ink)" }}>{rec.title}</div>
                              <div className="text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
                                {rec.author}
                                {rec.series ? <span style={{ color: "var(--color-faint)" }}> · {seriesLabel(rec.series, rec.series_number)}</span> : null}
                                {rec.words ? <span style={{ color: "var(--color-faint)" }}> · {formatWords(rec.words)} words</span> : null}
                              </div>
                            </td>
                            {hasMoods && (
                              <td className="px-3 py-3 text-right font-semibold" style={{ color: sortField === "mood" ? "var(--color-sage)" : (moodScore !== null ? "var(--color-ink)" : "var(--color-faint)"), background: sortField === "mood" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>
                                {moodScore !== null ? moodScore.toFixed(2) : "—"}
                              </td>
                            )}
                            <td className="px-3 py-3 text-right" style={{ color: sortField === "wa" ? "var(--color-sage)" : "var(--color-ink)", background: sortField === "wa" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>
                              <div>{rec.wa != null ? rec.wa.toFixed(2) : "—"}</div>
                              {formatInterval(rec) && (
                                <div className="text-xs" style={{ color: "var(--color-faint)" }}>{formatInterval(rec)}</div>
                              )}
                            </td>
                            <td className="px-3 py-3 text-right" style={{ color: sortField === "upside" ? "var(--color-sage)" : (rec.upside != null ? "var(--color-muted)" : "var(--color-faint)"), background: sortField === "upside" ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>
                              {rec.upside != null ? rec.upside.toFixed(2) : "—"}
                            </td>
                            {NF_CAT_ORDER.map((cat) => {
                              const val = avgs[cat] ?? 0;
                              const isActive = sortField === cat;
                              return (
                                <td key={cat} className="px-3 py-3 text-right" style={{ color: val === 0 ? "var(--color-faint)" : (isActive ? "var(--color-sage)" : "var(--color-muted)"), background: isActive ? "var(--color-sage-light)" : "transparent", fontVariantNumeric: "tabular-nums" }}>
                                  {val === 0 ? "—" : val.toFixed(2)}
                                </td>
                              );
                            })}
                            <td className="px-3 py-3"><span className="genre-chip">{rec.genre}</span></td>
                          </tr>
                          {isExpanded && (
                            <tr>
                              <td colSpan={colCount} style={{ padding: 0, borderBottom: "1px solid var(--color-rule)" }}>
                                <RecExpandedPanel
                                  rec={rec}
                                  moodScore={moodScore}
                                  onDelete={() => {
                                    setDeletedTitles((prev) => new Set([...prev, rec.title]));
                                    setExpandedTitle(null);
                                  }}
                                />
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
