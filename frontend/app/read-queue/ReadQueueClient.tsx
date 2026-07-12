"use client";

import React, { useState, useMemo, useCallback, useRef, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { ReadQueueResponse, Recommendation } from "@/lib/types";
import { saveQueue, generateRecommendationMeta, addSeriesToQueue, deleteRecommendation, updateRecommendationMetadata } from "@/lib/api";
import type { RecommendationMetadataPayload } from "@/lib/api";
import { seriesLabel } from "@/lib/format";
import { READONLY } from "@/lib/readonly";

/* ── Mood engine constants (mirrors app.py MOODS exactly) ──────────────── */
const MOOD_COMPONENTS: Record<string, string[]> = {
  "Action-Heavy":    ["Action", "Plot", "Entertainment"],
  "Theme-Heavy":     ["Insights", "Thought-Provokingness"],
  "Emotion-Heavy":   ["Emotional Impact", "Depth"],
  "Immersion-Heavy": ["Depth2", "Originality", "Prose"],
  "Story-Heavy":     ["Plot", "Ending", "Entertainment"],
  "Character-Heavy": ["Depth", "Motivations"],
};
const MOOD_NAMES = Object.keys(MOOD_COMPONENTS);

/* ── Helpers ──────────────────────────────────────────────────────────── */

function formatWords(words: number | null): string | null {
  if (!words) return null;
  if (words >= 1_000_000) return `${(words / 1_000_000).toFixed(1)}M`;
  if (words >= 1_000) return `${Math.round(words / 1_000)}K`;
  return `${words}`;
}

/** The 80% prediction range "7.9–9.6", or null when no interval is present. The
 *  point WA is a shrunk expected value; this shows the calibrated spread. */
function formatInterval(rec: Recommendation): string | null {
  if (rec.wa_low == null || rec.wa_high == null) return null;
  return `${rec.wa_low.toFixed(1)}–${rec.wa_high.toFixed(1)}`;
}

function moodScoreFor(rec: Recommendation, active: Record<string, number>): number | null {
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

/* ── Sub-components ───────────────────────────────────────────────────── */

function MoodInput({
  name,
  value,
  onChange,
}: {
  name: string;
  value: number;
  onChange: (v: number) => void;
}) {
  const comps = MOOD_COMPONENTS[name];
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
        <span
          className="text-sm font-semibold leading-tight"
          style={{ color: active ? "var(--color-sage)" : "var(--color-ink)", fontFamily: "var(--font-display)" }}
        >
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
          <span
            className="w-5 text-center text-sm font-bold tabular-nums"
            style={{ color: active ? "var(--color-sage)" : "var(--color-muted)", fontFamily: "var(--font-display)" }}
          >
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
        {comps.join(" · ")}
      </p>
    </div>
  );
}

/* ── Sort types ───────────────────────────────────────────────────────── */

type RecSortField = "mood" | "wa" | "upside" | "Story" | "Character" | "Aesthetics" | "Theme" | "Worldbuilding";
type RecSortDir = "desc" | "asc";

const CAT_COLS = ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"] as const;

function getRecSortValue(rec: Recommendation, moodScore: number | null, field: RecSortField): number {
  if (field === "mood") return moodScore ?? -Infinity;
  if (field === "wa") return rec.wa;
  // Upside = a good outcome (~76th percentile), not the interval ceiling. The
  // point estimate under-rates the top (regression to the mean), so ranking by
  // upside surfaces under-predicted candidates — at a result you'd beat ~1 in 4,
  // not the ~1-in-10 best case. Falls back to the point.
  if (field === "upside") return rec.upside ?? rec.wa;
  return (rec.category_avgs ?? {})[field] ?? 0;
}

function RecSortHeader({
  field,
  label,
  active,
  dir,
  onClick,
}: {
  field: RecSortField;
  label: string;
  active: boolean;
  dir: RecSortDir;
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

function RecExpandedPanel({
  rec,
  moodScore,
  hasMoods,
  genres,
  onDelete,
}: {
  rec: Recommendation;
  moodScore: number | null;
  hasMoods: boolean;
  genres: string[];
  onDelete: () => void;
}) {
  const router = useRouter();
  const [blurb, setBlurb] = useState(rec.blurb);
  const [keywords, setKeywords] = useState(rec.keywords);
  const [genError, setGenError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // ── Edit metadata (author/genre/series/series_number/words — no title/year) ──
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const emptyForm = useCallback(() => ({
    author: rec.author ?? "",
    genre: rec.genre ?? "",
    series: rec.series ?? "",
    series_number: rec.series_number != null ? String(rec.series_number) : "",
    words: rec.words != null ? String(rec.words) : "",
  }), [rec]);
  const [form, setForm] = useState(emptyForm);

  function startEdit() {
    setForm(emptyForm());
    setSaveError(null);
    setEditing(true);
  }

  const setNumField = (k: "series_number" | "words") =>
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value;
      if (raw === "" || /^\d*\.?\d*$/.test(raw)) setForm((f) => ({ ...f, [k]: raw }));
    };

  // Only send fields the user actually changed (omit-unchanged, matching the
  // ranked-book metadata editor). Blank numeric = leave the stored value as-is.
  function buildRecPayload(): RecommendationMetadataPayload {
    const p: RecommendationMetadataPayload = {};
    const a = form.author.trim();
    if (a && a !== (rec.author ?? "")) p.author = a;
    if (form.genre && form.genre !== rec.genre) p.genre = form.genre;
    const s = form.series.trim();
    if (s && s !== (rec.series ?? "")) p.series = s;
    const sn = form.series_number.trim();
    if (sn !== "") { const v = parseFloat(sn); if (!isNaN(v) && v !== rec.series_number) p.series_number = v; }
    const w = form.words.trim();
    if (w !== "") { const v = parseInt(w, 10); if (!isNaN(v) && v !== rec.words) p.words = v; }
    return p;
  }

  async function handleSaveMeta() {
    const payload = buildRecPayload();
    if (Object.keys(payload).length === 0) { setEditing(false); return; }
    setSaving(true);
    setSaveError(null);
    try {
      await updateRecommendationMetadata(rec.title, payload);
      setEditing(false);
      router.refresh(); // refetch so the row, this panel, and the re-weighted WA update
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  const metaInputStyle: React.CSSProperties = {
    background: "var(--color-surface)",
    borderColor: "var(--color-rule)",
    color: "var(--color-ink)",
  };
  const metaInputCls = "w-full px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2";
  const genreOptions = Array.from(new Set([...genres, ...(rec.genre ? [rec.genre] : [])])).sort();

  function handleGenerate() {
    setGenError(null);
    startTransition(async () => {
      try {
        const result = await generateRecommendationMeta(rec.title, rec.author, rec.genre);
        setBlurb(result.blurb);
        setKeywords(result.keywords);
      } catch (e) {
        setGenError(e instanceof Error ? e.message : "Generation failed.");
      }
    });
  }

  return (
    <div
      className="px-5 py-4 space-y-4"
      style={{ background: "var(--color-surface-2)", borderTop: "1px solid var(--color-rule)" }}
    >
      {/* Stats row */}
      <div className="flex flex-wrap gap-4 text-sm" style={{ color: "var(--color-muted)" }}>
        {hasMoods && moodScore !== null && (
          <span>
            Mood score: <strong style={{ color: "var(--color-sage)" }}>{moodScore.toFixed(2)}</strong>
          </span>
        )}
        <span>
          Predicted WA: <strong style={{ color: "var(--color-ink)" }}>{rec.wa.toFixed(2)}</strong>
          {rec.wa_low != null && rec.wa_high != null && (
            <span style={{ color: "var(--color-faint)" }}>
              {" "}· 80% likely {rec.wa_low.toFixed(1)}–{rec.wa_high.toFixed(1)}
              {rec.interval_label ? ` (${rec.interval_label})` : ""}
            </span>
          )}
        </span>
        <span className="genre-chip">{rec.genre}</span>
        {rec.words && (
          <span>
            Words: <strong style={{ color: "var(--color-ink)" }}>{rec.words.toLocaleString()}</strong>
          </span>
        )}
        {rec.series && (
          <span style={{ color: "var(--color-faint)" }}>Series: {seriesLabel(rec.series, rec.series_number)}</span>
        )}
      </div>

      {/* Blurb */}
      {blurb && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>
            Blurb
          </p>
          <p className="text-sm leading-relaxed" style={{ color: "var(--color-ink)" }}>
            {blurb}
          </p>
        </div>
      )}

      {/* Keywords */}
      {keywords && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: "var(--color-muted)" }}>
            Keywords
          </p>
          <div className="flex flex-wrap gap-1.5">
            {keywords.split(",").map((kw) => kw.trim()).filter(Boolean).map((kw) => (
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

      {/* Generate blurb & keywords (LLM spend — hidden on a read-only deploy) */}
      {!READONLY && !blurb && !keywords && (
        <div>
          <button
            onClick={handleGenerate}
            disabled={isPending}
            className="text-sm px-3 py-1.5 rounded-lg transition-colors"
            style={{
              background: isPending ? "var(--color-sage-light)" : "var(--color-surface-2)",
              color: isPending ? "var(--color-sage)" : "var(--color-muted)",
              border: "1px solid var(--color-rule)",
            }}
          >
            {isPending ? "Generating…" : "Generate blurb & keywords"}
          </button>
          {genError && (
            <p className="mt-2 text-xs" style={{ color: "#B45309" }}>{genError}</p>
          )}
        </div>
      )}

      {/* Component scores */}
      <ComponentScores components={rec.components} />

      {/* Edit metadata + remove (mutations — hidden on a read-only deploy) */}
      {!READONLY && (
      <div className="pt-2 border-t space-y-3" style={{ borderColor: "var(--color-rule)" }}>
        {editing && (
          <div className="space-y-2">
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(10rem, 1fr))" }}>
              <label className="block">
                <span className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>Author</span>
                <input type="text" value={form.author}
                  onChange={(e) => setForm((f) => ({ ...f, author: e.target.value }))}
                  className={metaInputCls} style={metaInputStyle} />
              </label>
              <label className="block">
                <span className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>Genre</span>
                <select value={form.genre}
                  onChange={(e) => setForm((f) => ({ ...f, genre: e.target.value }))}
                  className={metaInputCls} style={metaInputStyle}>
                  {!form.genre && <option value="">— choose —</option>}
                  {genreOptions.map((g) => <option key={g} value={g}>{g}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>Series</span>
                <input type="text" value={form.series}
                  onChange={(e) => setForm((f) => ({ ...f, series: e.target.value }))}
                  className={metaInputCls} style={metaInputStyle} />
              </label>
              <label className="block">
                <span className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>Series #</span>
                <input type="text" inputMode="decimal" value={form.series_number}
                  onChange={setNumField("series_number")}
                  className={metaInputCls} style={metaInputStyle} />
              </label>
              <label className="block">
                <span className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>Words</span>
                <input type="text" inputMode="numeric" value={form.words}
                  onChange={setNumField("words")}
                  className={metaInputCls} style={metaInputStyle} />
              </label>
            </div>
            <p className="text-xs" style={{ color: "var(--color-faint)" }}>
              Blank leaves a field unchanged. Changing the genre re-weights the predicted WA. The title isn’t editable here.
            </p>
          </div>
        )}

        <div className="flex items-center gap-3">
          {!editing && !deleteConfirm && (
            <button
              onClick={startEdit}
              className="text-xs px-3 py-1.5 rounded-lg transition-colors"
              style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
            >
              Edit details
            </button>
          )}

          {editing && (
            <>
              <button
                onClick={handleSaveMeta}
                disabled={saving}
                className="text-xs px-3 py-1.5 rounded-lg font-semibold disabled:opacity-40 transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                {saving ? "Saving…" : "Save changes"}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="text-xs px-3 py-1.5 rounded-lg"
                style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
              >
                Cancel
              </button>
              {saveError && <span className="text-xs" style={{ color: "#c0392b" }}>{saveError}</span>}
            </>
          )}

          {!editing && (!deleteConfirm ? (
            <button
              onClick={() => setDeleteConfirm(true)}
              className="text-xs px-3 py-1.5 rounded-lg transition-colors"
              style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
            >
              Remove from TBR
            </button>
          ) : (
            <>
              <span className="text-xs" style={{ color: "#c0392b" }}>Remove permanently?</span>
              <button
                onClick={async () => {
                  setIsDeleting(true);
                  setDeleteError(null);
                  try {
                    await deleteRecommendation(rec.title);
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
                style={{ background: "#c0392b", color: "#fff" }}
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
          {deleteError && (
            <span className="text-xs" style={{ color: "#c0392b" }}>{deleteError}</span>
          )}
        </div>
      </div>
      )}
    </div>
  );
}

function ComponentScores({ components }: { components: Record<string, number | null> }) {
  // Group by category order matching the Python engine
  const CATEGORIES: Record<string, string[]> = {
    Story:         ["Plot", "Entertainment", "Action", "Ending"],
    Character:     ["Depth", "Emotional Impact", "Motivations"],
    Aesthetics:    ["Prose", "Narration"],
    Theme:         ["Insights", "Thought-Provokingness"],
    Worldbuilding: ["Depth2", "Integration", "Originality"],
  };
  const CAT_ORDER = ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"];

  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
        Component Scores
      </p>
      {CAT_ORDER.map((cat) => {
        const comps = CATEGORIES[cat];
        const hasAny = comps.some((c) => components[c] !== null && components[c] !== undefined);
        if (!hasAny) return null;
        return (
          <div key={cat}>
            <p className="text-xs uppercase tracking-wider mb-1.5" style={{ color: "var(--color-faint)" }}>
              {cat}
            </p>
            <div
              className="grid gap-1.5"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(5rem, 1fr))" }}
            >
              {comps.map((comp) => {
                const v = components[comp];
                return (
                  <div key={comp} className="comp-tile">
                    <span className="comp-label">{comp}</span>
                    <span className="comp-value">
                      {v !== null && v !== undefined ? v.toFixed(1) : "—"}
                    </span>
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

/* ── Input helper ─────────────────────────────────────────────────────── */

function FilterSelect({
  value,
  onChange,
  children,
}: {
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-rule)",
        color: "var(--color-ink)",
        fontFamily: "var(--font-body)",
      }}
    >
      {children}
    </select>
  );
}

function FilterText({
  placeholder,
  value,
  onChange,
}: {
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 min-w-0"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-rule)",
        color: "var(--color-ink)",
        fontFamily: "var(--font-body)",
      }}
    />
  );
}

/* ── Queue tab ────────────────────────────────────────────────────────── */

function QueueExpandedPanel({ rec, rank }: { rec: Recommendation; rank: number }) {
  return (
    <div
      className="px-5 py-4 space-y-4"
      style={{ background: "var(--color-surface-2)", borderTop: "1px solid var(--color-rule)" }}
    >
      <div className="flex flex-wrap gap-4 text-sm" style={{ color: "var(--color-muted)" }}>
        <span>
          Predicted WA: <strong style={{ color: "var(--color-ink)" }}>{rec.wa.toFixed(2)}</strong>
          {rec.wa_low != null && rec.wa_high != null && (
            <span style={{ color: "var(--color-faint)" }}>
              {" "}· 80% likely {rec.wa_low.toFixed(1)}–{rec.wa_high.toFixed(1)}
              {rec.interval_label ? ` (${rec.interval_label})` : ""}
            </span>
          )}
        </span>
        <span>
          Predicted rank: <strong style={{ color: "var(--color-ink)" }}>#{rec.predicted_rank}</strong>
        </span>
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
  title,
  rec,
  rank,
  isDragging,
  isOver,
  isExpanded,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onRemove,
  onToggleExpand,
}: {
  title: string;
  rec: Recommendation | undefined;
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
        borderLeft: `3px solid ${isOver ? "var(--color-sage)" : isExpanded ? "var(--color-sage)" : "var(--color-rule)"}`,
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
      <div
        className="px-4 py-3 flex items-center gap-3 select-none cursor-pointer"
        onClick={onToggleExpand}
      >
        {/* Drag handle — stops propagation so dragging doesn't also toggle expand */}
        <svg
          className="w-4 h-4 flex-shrink-0"
          style={{ color: "var(--color-faint)", cursor: "grab" }}
          fill="currentColor"
          viewBox="0 0 20 20"
          onClick={(e) => e.stopPropagation()}
        >
          <path d="M7 4a1 1 0 100-2 1 1 0 000 2zM7 8a1 1 0 100-2 1 1 0 000 2zM7 12a1 1 0 100-2 1 1 0 000 2zM7 16a1 1 0 100-2 1 1 0 000 2zM13 4a1 1 0 100-2 1 1 0 000 2zM13 8a1 1 0 100-2 1 1 0 000 2zM13 12a1 1 0 100-2 1 1 0 000 2zM13 16a1 1 0 100-2 1 1 0 000 2z" />
        </svg>

        {/* Rank badge */}
        <div className="wa-badge flex-shrink-0" style={{ background: rank === 1 ? "var(--color-sage)" : "var(--color-faint)", minWidth: "2.2rem" }}>
          #{rank}
        </div>

        {/* Title / author / series */}
        <div className="flex-1 min-w-0">
          <h3
            className="font-display font-semibold text-base leading-tight truncate"
            style={{ color: "var(--color-ink)" }}
          >
            {title}
          </h3>
          {rec && (
            <p className="text-sm mt-0.5 truncate" style={{ color: "var(--color-muted)" }}>
              {rec.author}
              {rec.series ? (
                <span style={{ color: "var(--color-faint)" }}> · {seriesLabel(rec.series, rec.series_number)}</span>
              ) : null}
            </p>
          )}
        </div>

        {/* Genre + words */}
        {rec && (
          <div className="hidden sm:flex flex-col items-end gap-1 flex-shrink-0">
            <span className="genre-chip">{rec.genre}</span>
            {words && (
              <span className="text-xs" style={{ color: "var(--color-faint)" }}>{words} words</span>
            )}
          </div>
        )}

        {/* Predicted WA inline (collapsed) */}
        {rec && !isExpanded && (
          <div className="hidden sm:flex flex-col items-end flex-shrink-0 ml-1">
            <span className="text-xs font-semibold tabular-nums" style={{ color: "var(--color-sage)" }}>
              {rec.wa.toFixed(2)}
            </span>
            <span className="text-xs" style={{ color: "var(--color-faint)" }}>pred WA</span>
          </div>
        )}

        {/* Chevron */}
        <svg
          className="w-4 h-4 flex-shrink-0 transition-transform"
          style={{ color: "var(--color-faint)", transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)" }}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>

        {/* Remove */}
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="flex-shrink-0 w-7 h-7 rounded flex items-center justify-center text-base font-bold leading-none transition-colors"
          style={{
            background: "var(--color-surface-2)",
            color: "var(--color-muted)",
            border: "1px solid var(--color-rule)",
          }}
          aria-label={`Remove ${title}`}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "#c0392b"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--color-muted)"; }}
        >
          ×
        </button>
      </div>

      {/* Expanded prediction panel */}
      {isExpanded && (
        rec ? (
          <QueueExpandedPanel rec={rec} rank={rank} />
        ) : (
          <div
            className="px-5 py-4"
            style={{ background: "var(--color-surface-2)", borderTop: "1px solid var(--color-rule)" }}
          >
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              No prediction yet —{" "}
              <a href={`/predict?title=${encodeURIComponent(title)}`} style={{ color: "var(--color-sage)" }}>
                research on Predict page
              </a>
            </p>
          </div>
        )
      )}
    </article>
  );
}

function QueueTab({
  initialQueue,
  recommendations,
}: {
  initialQueue: string[];
  recommendations: Recommendation[];
}) {
  const [items, setItems] = useState<string[]>(initialQueue);
  const [textMode, setTextMode] = useState(false);
  const [text, setText] = useState(() => initialQueue.join("\n"));
  const [addInput, setAddInput] = useState("");
  const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const savedRef = useRef<string[]>(initialQueue);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);
  const [expandedQueueTitle, setExpandedQueueTitle] = useState<string | null>(null);
  const [seriesInput, setSeriesInput] = useState("");
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [seriesStatus, setSeriesStatus] = useState<{ ok: boolean; msg: string } | null>(null);

  const recByTitle = useMemo(() => {
    const m = new Map<string, Recommendation>();
    for (const r of recommendations) m.set(r.title, r);
    return m;
  }, [recommendations]);

  const isDirty = JSON.stringify(items) !== JSON.stringify(savedRef.current);

  async function handleSave(overrideTitles?: string[]) {
    setSaving(true);
    setStatus(null);
    try {
      const titles = overrideTitles ?? items;
      const res = await saveQueue(titles);
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

  function handleDragStart(idx: number) {
    setDragIdx(idx);
  }

  function handleDragOver(e: React.DragEvent, idx: number) {
    e.preventDefault();
    setDragOverIdx(idx);
  }

  function handleDrop(idx: number) {
    if (dragIdx === null || dragIdx === idx) {
      setDragIdx(null);
      setDragOverIdx(null);
      return;
    }
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

  function handleDragEnd() {
    setDragIdx(null);
    setDragOverIdx(null);
  }

  async function handleAddSeries() {
    const name = seriesInput.trim();
    if (!name) return;
    setSeriesLoading(true);
    setSeriesStatus(null);
    try {
      const result = await addSeriesToQueue(name);
      if (result.ambiguous || !result.ok) {
        setSeriesStatus({ ok: false, msg: result.message });
      } else {
        setSeriesStatus({ ok: true, msg: result.message });
        if (result.appended_titles && result.appended_titles.length > 0) {
          setItems((prev) => [...prev, ...result.appended_titles!.filter((t) => !prev.includes(t))]);
          savedRef.current = [...savedRef.current, ...result.appended_titles!.filter((t) => !savedRef.current.includes(t))];
        }
        setSeriesInput("");
      }
    } catch (e: unknown) {
      setSeriesStatus({ ok: false, msg: e instanceof Error ? e.message : "Failed to add series." });
    } finally {
      setSeriesLoading(false);
    }
  }

  function toggleTextMode() {
    if (!textMode) {
      setText(items.join("\n"));
    }
    setTextMode((m) => !m);
    setStatus(null);
  }

  if (textMode) {
    const lines = text.split("\n").filter((t) => t.trim()).length;
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            One title per line — top is next up.
          </p>
          <button
            onClick={toggleTextMode}
            className="text-xs px-3 py-1.5 rounded-lg"
            style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
          >
            Card view
          </button>
        </div>
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setStatus(null); }}
          rows={18}
          className="w-full rounded-xl px-4 py-3 text-sm font-mono focus:outline-none focus:ring-2 resize-y"
          style={{
            background: "var(--color-surface)",
            border: "1px solid var(--color-rule)",
            color: "var(--color-ink)",
            lineHeight: "1.7",
          }}
          placeholder="Paste or type one book title per line…"
          spellCheck={false}
        />
        <div className="flex items-center gap-3">
          <button
            onClick={handleTextSave}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity"
            style={{ background: "var(--color-sage)", color: "#fff" }}
          >
            {saving ? "Saving…" : "Save queue"}
          </button>
          <span className="text-xs" style={{ color: "var(--color-faint)" }}>
            {lines} title{lines !== 1 ? "s" : ""}
          </span>
          {status && (
            <span className="text-sm" style={{ color: status.ok ? "var(--color-sage)" : "#c0392b" }}>
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
        <button
          onClick={toggleTextMode}
          className="text-xs px-3 py-1.5 rounded-lg flex-shrink-0"
          style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
        >
          Edit as text
        </button>
      </div>

      {/* Card list */}
      <div className="space-y-2">
        {items.length === 0 && (
          <p className="text-sm text-center py-8" style={{ color: "var(--color-faint)" }}>
            Queue is empty — add a book below.
          </p>
        )}
        {items.map((title, i) => (
          <QueueCard
            key={title}
            title={title}
            rec={recByTitle.get(title)}
            rank={i + 1}
            isDragging={dragIdx === i}
            isOver={dragOverIdx === i}
            isExpanded={expandedQueueTitle === title}
            onDragStart={() => handleDragStart(i)}
            onDragOver={(e) => handleDragOver(e, i)}
            onDrop={() => handleDrop(i)}
            onDragEnd={handleDragEnd}
            onRemove={() => handleRemove(i)}
            onToggleExpand={() => setExpandedQueueTitle((t) => t === title ? null : title)}
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
          style={{
            background: "var(--color-surface)",
            border: "1px solid var(--color-rule)",
            color: "var(--color-ink)",
            fontFamily: "var(--font-body)",
          }}
        />
        <button
          onClick={handleAdd}
          disabled={!addInput.trim()}
          className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity"
          style={{ background: "var(--color-surface-2)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}
        >
          Add
        </button>
      </div>

      {/* Save bar */}
      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={() => handleSave()}
          disabled={saving || !isDirty}
          className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity"
          style={{ background: "var(--color-sage)", color: "#fff" }}
        >
          {saving ? "Saving…" : "Save queue"}
        </button>
        <span className="text-xs" style={{ color: "var(--color-faint)" }}>
          {items.length} title{items.length !== 1 ? "s" : ""}
          {isDirty ? " · unsaved changes" : ""}
        </span>
        {status && (
          <span className="text-sm" style={{ color: status.ok ? "var(--color-sage)" : "#c0392b" }}>
            {status.ok ? "✓ " : "✗ "}{status.msg}
          </span>
        )}
      </div>

      {/* Add series */}
      <div
        className="rounded-xl p-4 space-y-3 mt-2"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
          Add series to queue
        </p>
        <p className="text-xs" style={{ color: "var(--color-faint)" }}>
          Type a series name and all unread books will be appended in reading order. Missing books are added to your TBR first.
        </p>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="e.g. Mistborn Era 1, The Stormlight Archive…"
            value={seriesInput}
            onChange={(e) => { setSeriesInput(e.target.value); setSeriesStatus(null); }}
            onKeyDown={(e) => { if (e.key === "Enter" && !seriesLoading) handleAddSeries(); }}
            disabled={seriesLoading}
            className="flex-1 px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 min-w-0 disabled:opacity-50"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-rule)",
              color: "var(--color-ink)",
              fontFamily: "var(--font-body)",
            }}
          />
          <button
            onClick={handleAddSeries}
            disabled={!seriesInput.trim() || seriesLoading}
            className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity whitespace-nowrap"
            style={{ background: "var(--color-surface-2)", color: "var(--color-ink)", border: "1px solid var(--color-rule)" }}
          >
            {seriesLoading ? "Adding…" : "Add series"}
          </button>
        </div>
        {seriesStatus && (
          <p className="text-sm" style={{ color: seriesStatus.ok ? "var(--color-sage)" : "#c0392b" }}>
            {seriesStatus.ok ? "✓ " : "✗ "}{seriesStatus.msg}
          </p>
        )}
      </div>
    </div>
  );
}

/* ── Main client ──────────────────────────────────────────────────────── */

export default function ReadQueueClient({
  data,
  initialQueue,
}: {
  data: ReadQueueResponse;
  initialQueue: string[];
}) {
  const { recommendations, genres } = data;

  const [deletedTitles, setDeletedTitles] = useState<Set<string>>(new Set());

  /* Mood weights */
  const [moodWeights, setMoodWeights] = useState<Record<string, number>>(
    () => Object.fromEntries(MOOD_NAMES.map((m) => [m, 0]))
  );

  const setMood = useCallback((name: string, v: number) => {
    setMoodWeights((prev) => ({ ...prev, [name]: v }));
  }, []);

  /* Filters */
  const [fGenre, setFGenre] = useState("All genres");
  const [fLength, setFLength] = useState("Any");
  const [fType, setFType] = useState("Any");
  const [fAuthor, setFAuthor] = useState("");
  const [fKeyword, setFKeyword] = useState("");

  /* Derived: component-level weight aggregation */
  const active = useMemo(() => {
    const impl: Record<string, number> = {};
    for (const [mood, comps] of Object.entries(MOOD_COMPONENTS)) {
      const w = moodWeights[mood] ?? 0;
      if (w <= 0) continue;
      for (const c of comps) {
        impl[c] = (impl[c] ?? 0) + w;
      }
    }
    return impl;
  }, [moodWeights]);

  const hasMoods = Object.keys(active).length > 0;

  /* Filtered + scored + sorted list */
  const results = useMemo(() => {
    let list = recommendations.filter((r) => !deletedTitles.has(r.title));

    if (fGenre !== "All genres") {
      list = list.filter((r) => r.genre === fGenre);
    }
    if (fLength !== "Any") {
      if (fLength === "Short (<150K)") {
        list = list.filter((r) => r.words !== null && r.words < 150_000);
      } else if (fLength === "Medium (150–300K)") {
        list = list.filter((r) => r.words !== null && r.words >= 150_000 && r.words <= 300_000);
      } else {
        list = list.filter((r) => r.words !== null && r.words > 300_000);
      }
    }
    if (fType !== "Any") {
      list = list.filter((r) =>
        fType === "Series" ? r.series.length > 0 : r.series.length === 0
      );
    }
    if (fAuthor.trim()) {
      const q = fAuthor.trim().toLowerCase();
      list = list.filter((r) => r.author.toLowerCase().includes(q));
    }
    if (fKeyword.trim()) {
      const q = fKeyword.trim().toLowerCase();
      list = list.filter((r) => r.keywords.toLowerCase().includes(q));
    }

    // Score (sort happens separately so it can be client-controlled)
    return list.map((r) => ({
      rec: r,
      moodScore: hasMoods ? moodScoreFor(r, active) : null,
    }));
  }, [recommendations, deletedTitles, fGenre, fLength, fType, fAuthor, fKeyword, hasMoods, active]);

  const resetMoods = useCallback(() => {
    setMoodWeights(Object.fromEntries(MOOD_NAMES.map((m) => [m, 0])));
  }, []);

  const [tab, setTab] = useState<"mood" | "queue">("mood");
  const [sortField, setSortField] = useState<RecSortField>("mood");
  const [sortDir, setSortDir] = useState<RecSortDir>("desc");
  const [expandedTitle, setExpandedTitle] = useState<string | null>(null);

  function handleSortClick(field: RecSortField) {
    if (field === sortField) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  }

  const sortedResults = useMemo(() => {
    const mult = sortDir === "desc" ? -1 : 1;
    return [...results].sort((a, b) => {
      if (sortField === "mood" && !hasMoods) {
        return a.rec.predicted_rank - b.rec.predicted_rank;
      }
      return mult * (getRecSortValue(a.rec, a.moodScore, sortField) - getRecSortValue(b.rec, b.moodScore, sortField));
    });
  }, [results, sortField, sortDir, hasMoods]);

  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Read Queue
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {recommendations.length - deletedTitles.size} book{(recommendations.length - deletedTitles.size) !== 1 ? "s" : ""} in your to-read list · {initialQueue.length} in your ordered queue
        </p>
      </div>

      {/* Tab bar — the Queue tab is an editor (reorder/remove/add), so it's
          hidden on a read-only deploy; Mood Scores stays as a view. */}
      <div
        className="flex gap-1 mb-6 p-1 rounded-xl w-fit"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        {(READONLY ? (["mood"] as const) : (["mood", "queue"] as const)).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: tab === t ? "var(--color-sage)" : "transparent",
              color: tab === t ? "#fff" : "var(--color-muted)",
            }}
          >
            {t === "mood" ? "Mood Scores" : "Queue"}
          </button>
        ))}
      </div>

      {!READONLY && tab === "queue" && <QueueTab initialQueue={initialQueue} recommendations={recommendations} />}

      {tab === "mood" && recommendations.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
        >
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            No recommendations yet — research books on the Predict page to add them here.
          </p>
        </div>
      ) : tab === "mood" ? (
        <>
          {/* ── Mood section ─────────────────────────────────────────────── */}
          <section
            className="mb-6 rounded-xl p-5"
            style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
          >
            <div className="flex items-center justify-between mb-1">
              <h2 className="font-display font-semibold text-lg" style={{ color: "var(--color-ink)" }}>
                Mood
              </h2>
              {hasMoods && (
                <button
                  onClick={resetMoods}
                  className="text-xs px-2 py-1 rounded-lg"
                  style={{ color: "var(--color-muted)", background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}
                >
                  Reset all
                </button>
              )}
            </div>
            <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
              Dial up the moods you want — results re-rank to match. All at 0 falls back to predicted-rank order.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {MOOD_NAMES.map((name) => (
                <MoodInput
                  key={name}
                  name={name}
                  value={moodWeights[name] ?? 0}
                  onChange={(v) => setMood(name, v)}
                />
              ))}
            </div>
          </section>

          {/* ── Filters ──────────────────────────────────────────────────── */}
          <section
            className="mb-6 rounded-xl p-5"
            style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
          >
            <h2 className="font-display font-semibold text-lg mb-4" style={{ color: "var(--color-ink)" }}>
              Filters
            </h2>
            <div className="flex flex-wrap gap-3">
              <FilterSelect value={fGenre} onChange={setFGenre}>
                <option value="All genres">All genres</option>
                {genres.map((g) => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </FilterSelect>

              <FilterSelect value={fLength} onChange={setFLength}>
                <option value="Any">Any length</option>
                <option value="Short (<150K)">Short (&lt;150K)</option>
                <option value="Medium (150–300K)">Medium (150–300K)</option>
                <option value="Long (>300K)">Long (&gt;300K)</option>
              </FilterSelect>

              <FilterSelect value={fType} onChange={setFType}>
                <option value="Any">Any type</option>
                <option value="Series">Series</option>
                <option value="Standalone">Standalone</option>
              </FilterSelect>

              <FilterText
                placeholder="Author contains…"
                value={fAuthor}
                onChange={setFAuthor}
              />

              <FilterText
                placeholder="Keyword tag…"
                value={fKeyword}
                onChange={setFKeyword}
              />
            </div>
          </section>

          {/* ── Results ──────────────────────────────────────────────────── */}
          <section>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <h2
                  className="font-display font-semibold text-lg"
                  style={{ color: "var(--color-ink)" }}
                >
                  Results
                </h2>
                <div
                  className="px-3 py-1 rounded-full text-sm font-medium"
                  style={{ background: "var(--color-sage-light)", color: "var(--color-sage)" }}
                >
                  {results.length}
                </div>
              </div>
              <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                click a column header to sort · click a row to expand · <span title="A good outcome — the ~76th percentile, one you'd beat about 1 in 4 reads (not the interval ceiling, which is ~1 in 10). The point estimate under-rates the top, so sorting by Upside surfaces under-rated / frontier picks without assuming best-case for all of them.">Upside ≈ 76th-percentile outcome</span>
              </span>
            </div>

            {results.length === 0 ? (
              <p className="text-center py-10 text-sm" style={{ color: "var(--color-muted)" }}>
                No books match your filters.
              </p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                  <thead>
                    <tr style={{ background: "var(--color-surface)" }}>
                      <th
                        className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2"
                        style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)", minWidth: "2rem" }}
                      >
                        #
                      </th>
                      <th
                        className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2"
                        style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)", minWidth: "12rem" }}
                      >
                        Book
                      </th>
                      {/* Mood column — only shown when moods are active */}
                      {hasMoods && (
                        <RecSortHeader
                          field="mood"
                          label="Mood"
                          active={sortField === "mood"}
                          dir={sortDir}
                          onClick={() => handleSortClick("mood")}
                        />
                      )}
                      <RecSortHeader
                        field="wa"
                        label="Pred WA"
                        active={sortField === "wa"}
                        dir={sortDir}
                        onClick={() => handleSortClick("wa")}
                      />
                      <RecSortHeader
                        field="upside"
                        label="Upside"
                        active={sortField === "upside"}
                        dir={sortDir}
                        onClick={() => handleSortClick("upside")}
                      />
                      {CAT_COLS.map((cat) => (
                        <RecSortHeader
                          key={cat}
                          field={cat}
                          label={cat === "Aesthetics" ? "Aes" : cat === "Character" ? "Char" : cat === "Worldbuilding" ? "WB" : cat}
                          active={sortField === cat}
                          dir={sortDir}
                          onClick={() => handleSortClick(cat)}
                        />
                      ))}
                      <th
                        className="text-left text-xs font-semibold uppercase tracking-wider px-3 py-2"
                        style={{ color: "var(--color-muted)", borderBottom: "1px solid var(--color-rule)" }}
                      >
                        Genre
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedResults.map(({ rec, moodScore }, i) => {
                      const isExpanded = expandedTitle === rec.title;
                      const avgs = rec.category_avgs ?? {};
                      const colCount = 5 + CAT_COLS.length + (hasMoods ? 1 : 0);
                      return (
                        <React.Fragment key={rec.title}>
                          <tr
                            onClick={() => setExpandedTitle(isExpanded ? null : rec.title)}
                            className="cursor-pointer"
                            style={{
                              borderBottom: isExpanded ? "none" : "1px solid var(--color-rule)",
                              transition: "background 0.1s",
                            }}
                          >
                            <td
                              className="px-3 py-3 font-display italic text-sm text-right"
                              style={{ color: "var(--color-faint)", minWidth: "2.5rem" }}
                            >
                              {i + 1}
                            </td>
                            <td className="px-3 py-3" style={{ minWidth: "12rem" }}>
                              <div
                                className="font-display font-semibold text-sm leading-tight"
                                style={{ color: "var(--color-ink)" }}
                              >
                                {rec.title}
                              </div>
                              <div className="text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
                                {rec.author}
                                {rec.series ? <span style={{ color: "var(--color-faint)" }}> · {seriesLabel(rec.series, rec.series_number)}</span> : null}
                                {rec.words ? <span style={{ color: "var(--color-faint)" }}> · {formatWords(rec.words)} words</span> : null}
                              </div>
                            </td>
                            {hasMoods && (
                              <td
                                className="px-3 py-3 text-right font-semibold"
                                style={{
                                  color: sortField === "mood" ? "var(--color-sage)" : (moodScore !== null ? "var(--color-ink)" : "var(--color-faint)"),
                                  background: sortField === "mood" ? "var(--color-sage-light)" : "transparent",
                                  fontVariantNumeric: "tabular-nums",
                                }}
                              >
                                {moodScore !== null ? moodScore.toFixed(2) : "—"}
                              </td>
                            )}
                            <td
                              className="px-3 py-3 text-right"
                              style={{
                                color: sortField === "wa" ? "var(--color-sage)" : "var(--color-muted)",
                                background: sortField === "wa" ? "var(--color-sage-light)" : "transparent",
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              <div>{rec.wa.toFixed(2)}</div>
                              {formatInterval(rec) && (
                                <div className="text-xs" style={{ color: "var(--color-faint)" }}>
                                  {formatInterval(rec)}
                                </div>
                              )}
                            </td>
                            <td
                              className="px-3 py-3 text-right"
                              style={{
                                color: sortField === "upside" ? "var(--color-sage)" : (rec.upside != null ? "var(--color-muted)" : "var(--color-faint)"),
                                background: sortField === "upside" ? "var(--color-sage-light)" : "transparent",
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {rec.upside != null ? rec.upside.toFixed(2) : "—"}
                            </td>
                            {CAT_COLS.map((cat) => {
                              const val = avgs[cat] ?? 0;
                              const isActive = sortField === cat;
                              return (
                                <td
                                  key={cat}
                                  className="px-3 py-3 text-right"
                                  style={{
                                    color: val === 0 ? "var(--color-faint)" : (isActive ? "var(--color-sage)" : "var(--color-muted)"),
                                    background: isActive ? "var(--color-sage-light)" : "transparent",
                                    fontVariantNumeric: "tabular-nums",
                                  }}
                                >
                                  {val === 0 ? "—" : val.toFixed(2)}
                                </td>
                              );
                            })}
                            <td className="px-3 py-3">
                              <span className="genre-chip">{rec.genre}</span>
                            </td>
                          </tr>
                          {isExpanded && (
                            <tr>
                              <td
                                colSpan={colCount}
                                style={{ padding: 0, borderBottom: "1px solid var(--color-rule)" }}
                              >
                                <RecExpandedPanel
                                  rec={rec}
                                  moodScore={moodScore}
                                  hasMoods={hasMoods}
                                  genres={genres}
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
