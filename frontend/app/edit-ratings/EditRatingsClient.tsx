"use client";

import { useState, useEffect, useRef } from "react";
import { fetchBookScores, editRating } from "@/lib/api";
import type { BooksResponse, CategoryComponents } from "@/lib/types";
import { seriesLabel } from "@/lib/format";

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

/* ── Score input helpers ── raw string state so a box can go empty without
   snapping back to 0; empty means "leave unchanged" (see handleSave). ────── */

const SCORE_INPUT_RE = /^-?\d*\.?\d*$/;

function clampScoreInput(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === "") return raw;
  const v = parseFloat(trimmed);
  if (isNaN(v)) return raw;
  const clamped = Math.min(10, Math.max(0, v));
  return clamped === v ? raw : String(clamped);
}

/** Builds a payload of only the fields that have a real, parseable value —
 * empty/unparseable boxes are omitted so change_rating() leaves them unchanged. */
function buildChangedScores(raw: Record<string, string>): Record<string, number> {
  const payload: Record<string, number> = {};
  for (const [comp, str] of Object.entries(raw)) {
    const trimmed = str.trim();
    if (trimmed === "") continue;
    const v = parseFloat(trimmed);
    if (isNaN(v)) continue;
    payload[comp] = Math.min(10, Math.max(0, v));
  }
  return payload;
}

/* ── Component score grid ── mirrors Rankings detail view ────────────────── */

function ScoreGrid({
  components,
  categoryOrder,
  scores,
  onChange,
}: {
  components: CategoryComponents;
  categoryOrder: string[];
  scores: Record<string, string>;
  onChange: (comp: string, val: string) => void;
}) {
  return (
    <div className="space-y-5">
      {categoryOrder.map((cat) => {
        const comps = components[cat];
        if (!comps) return null;
        return (
          <div key={cat}>
            <p className="text-xs font-semibold uppercase tracking-widest mb-2"
              style={{ color: "var(--color-muted)" }}>
              {cat}
            </p>
            <div
              className="grid gap-3"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))" }}
            >
              {Object.keys(comps).map((comp) => (
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
        );
      })}
    </div>
  );
}

/* ── Main component ─────────────────────────────────────────────────────── */

export default function EditRatingsClient({ data }: { data: BooksResponse }) {
  const { books, category_order } = data;
  const titles = books.map((b) => b.title).sort();

  const [selectedTitle, setSelectedTitle] = useState<string>("");
  // Tracks which title's scores are currently loaded — avoids showing stale
  // scores from the previous selection while the new one is fetching.
  const loadedForRef = useRef<string>("");
  const [components, setComponents] = useState<CategoryComponents>({});
  const [scores, setScores] = useState<Record<string, string>>({});
  const [loadingScores, setLoadingScores] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

  // When the selected title changes, fetch that book's current scores.
  useEffect(() => {
    if (!selectedTitle) {
      setComponents({});
      setScores({});
      loadedForRef.current = "";
      return;
    }
    let cancelled = false;
    setLoadingScores(true);
    setLoadError(null);
    setSaveError(null);
    setSaveSuccess(null);
    // Clear scores immediately so stale values aren't editable while loading
    setComponents({});
    setScores({});
    fetchBookScores(selectedTitle)
      .then((result) => {
        if (cancelled) return;
        loadedForRef.current = selectedTitle;
        setComponents(result.components);
        // Flatten components into a single scores dict (raw display strings;
        // a missing/null score renders as an empty box, not 0).
        const flat: Record<string, string> = {};
        for (const comps of Object.values(result.components)) {
          for (const [comp, val] of Object.entries(comps)) {
            flat[comp] = val != null ? String(val) : "";
          }
        }
        setScores(flat);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : "Failed to load scores.");
      })
      .finally(() => {
        if (!cancelled) setLoadingScores(false);
      });
    return () => { cancelled = true; };
  }, [selectedTitle]);

  function handleScoreChange(comp: string, val: string) {
    setScores((prev) => ({ ...prev, [comp]: val }));
  }

  async function handleSave() {
    if (!selectedTitle || loadedForRef.current !== selectedTitle) return;
    // Empty boxes mean "leave unchanged" — only send fields with a real value.
    const payload = buildChangedScores(scores);
    if (Object.keys(payload).length === 0) {
      setSaveError("No changes to save — enter a value in at least one field.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(null);
    try {
      const result = await editRating(selectedTitle, payload);
      setSaveSuccess(result.message || `Saved changes to "${selectedTitle}".`);
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  const selectedBook = books.find((b) => b.title === selectedTitle);
  const hasScores = Object.keys(scores).length > 0;

  return (
    <div>
      {/* Page header */}
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}>
          Edit Ratings
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          Select a book, adjust any component scores, and save.
        </p>
      </div>

      {/* Book selector */}
      <section
        className="rounded-xl p-5 mb-6"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <label className="block text-xs font-semibold uppercase tracking-widest mb-2"
          style={{ color: "var(--color-muted)" }}>
          Select a book
        </label>
        <select
          value={selectedTitle}
          onChange={(e) => setSelectedTitle(e.target.value)}
          className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
          style={inputStyle}
        >
          <option value="">— choose a book —</option>
          {titles.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        {selectedBook && (
          <div className="flex items-center gap-3 mt-3">
            <div className="wa-badge" style={{ width: "2.5rem", height: "2.5rem", fontSize: "0.75rem" }}>
              {selectedBook.wa.toFixed(2)}
            </div>
            <div>
              <p className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
                {selectedBook.author}
              </p>
              <p className="text-xs" style={{ color: "var(--color-muted)" }}>
                {selectedBook.genre}
                {selectedBook.series ? ` · ${seriesLabel(selectedBook.series, selectedBook.series_number)}` : ""}
              </p>
            </div>
            <span className="genre-chip ml-auto">{selectedBook.genre}</span>
          </div>
        )}
      </section>

      {/* Scores panel */}
      {selectedTitle && (
        <section
          className="rounded-xl p-5 mb-6"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
        >
          {loadingScores && (
            <p className="text-sm py-4 text-center" style={{ color: "var(--color-muted)" }}>
              Loading scores…
            </p>
          )}
          {loadError && (
            <div className="rounded-lg px-4 py-3 text-sm"
              style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}>
              {loadError}
            </div>
          )}
          {!loadingScores && !loadError && hasScores && (
            <ScoreGrid
              components={components}
              categoryOrder={category_order}
              scores={scores}
              onChange={handleScoreChange}
            />
          )}
        </section>
      )}

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

      {selectedTitle && hasScores && (
        <button
          onClick={handleSave}
          disabled={saving || loadingScores}
          className="px-6 py-3 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
          style={{ background: "var(--color-sage)", color: "#fff" }}
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
      )}
    </div>
  );
}
