"use client";

/* Mid-book reviews: quick checkpoints logged while reading the current book —
 * how far in, a 0–10 gut score, an optional note. DISPLAY ONLY: nothing here
 * changes a prediction. The gut score is placed against the served 80% band as
 * a rough read; a single gut number is not a WA, and the copy says so. */

import { useEffect, useState } from "react";
import type { BookKind, ReadingCheckpoint, StatusSlot } from "@/lib/types";
import { addCheckpoint, deleteCheckpoint, fetchCheckpoints } from "@/lib/api";
import {
  gapFromWa,
  parseGutScore,
  parseProgress,
  placeAgainstBand,
  sortCheckpoints,
  type CheckpointPlacement,
} from "@/lib/checkpoints";

const PLACEMENT_TEXT: Record<CheckpointPlacement, string> = {
  above: "above the predicted range",
  within: "inside the predicted range",
  below: "below the predicted range",
};

const PLACEMENT_COLOR: Record<CheckpointPlacement, string> = {
  above: "var(--color-sage)",
  within: "var(--color-muted)",
  below: "var(--color-danger)",
};

const fieldStyle = {
  background: "var(--color-surface-2)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
};

export function MidBookReviews({ slot, kind }: { slot: StatusSlot; kind: BookKind }) {
  const [checkpoints, setCheckpoints] = useState<ReadingCheckpoint[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [gut, setGut] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchCheckpoints(kind, slot.title)
      .then((cps) => { if (live) { setCheckpoints(cps); setLoadError(null); } })
      .catch((e: unknown) => { if (live) setLoadError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [kind, slot.title]);

  const hasBand = slot.wa_low != null && slot.wa_high != null;

  async function handleAdd() {
    const pct = parseProgress(progress);
    const score = parseGutScore(gut);
    if (pct == null) { setFormError("Progress must be a whole number from 0 to 100."); return; }
    if (score == null) { setFormError("Gut score must be a number from 0 to 10."); return; }
    setSaving(true);
    setFormError(null);
    try {
      const cp = await addCheckpoint({
        kind, title: slot.title, progress_pct: pct, gut_score: score,
        note: note.trim() || null,
      });
      setCheckpoints((prev) => [...(prev ?? []), cp]);
      setProgress(""); setGut(""); setNote("");
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteCheckpoint(id);
      setCheckpoints((prev) => (prev ?? []).filter((c) => c.id !== id));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div
      className="rounded-xl p-5 flex flex-col gap-4 sm:col-span-3"
      style={{ background: "var(--color-surface)", boxShadow: "var(--shadow-card)" }}
    >
      <div>
        <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
          Mid-book reviews
        </span>
        <p className="font-display font-bold text-lg leading-tight mt-1" style={{ color: "var(--color-ink)" }}>
          {slot.title}
        </p>
        <p className="text-xs mt-1" style={{ color: "var(--color-faint)" }}>
          {slot.wa != null
            ? hasBand
              ? `${slot.has_prediction ? "Predicted" : "Current"} WA ${slot.wa.toFixed(2)}, 80% range ${slot.wa_low!.toFixed(2)}–${slot.wa_high!.toFixed(2)}. `
              : `${slot.has_prediction ? "Predicted" : "Current"} WA ${slot.wa.toFixed(2)}. `
            : ""}
          A gut score is one number, not a full rating, so this is a rough read. It never changes the prediction.
        </p>
      </div>

      {/* Log form */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap gap-2">
          <label className="flex flex-col gap-1 text-xs" style={{ color: "var(--color-muted)" }}>
            Progress %
            <input
              type="number" inputMode="numeric" min={0} max={100} step={1}
              value={progress} onChange={(e) => setProgress(e.target.value)}
              className="w-24 px-2 py-1.5 rounded-lg text-sm focus:outline-none focus:ring-2"
              style={fieldStyle} placeholder="45"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs" style={{ color: "var(--color-muted)" }}>
            Gut score /10
            <input
              type="number" inputMode="decimal" min={0} max={10} step={0.5}
              value={gut} onChange={(e) => setGut(e.target.value)}
              className="w-24 px-2 py-1.5 rounded-lg text-sm focus:outline-none focus:ring-2"
              style={fieldStyle} placeholder="7.5"
            />
          </label>
        </div>
        <textarea
          value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000}
          className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 resize-y"
          style={fieldStyle} placeholder="How's it going? (optional)"
        />
        <div className="flex items-center gap-3">
          <button
            onClick={handleAdd} disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-opacity"
            style={{ background: "var(--color-sage)", color: "var(--color-on-accent)" }}
          >
            {saving ? "Saving…" : "Log checkpoint"}
          </button>
          {formError && (
            <span className="text-sm" style={{ color: "var(--color-danger)" }}>✗ {formError}</span>
          )}
        </div>
      </div>

      {/* Journal */}
      {loadError ? (
        <p className="text-sm" style={{ color: "var(--color-danger)" }}>Couldn&apos;t load checkpoints: {loadError}</p>
      ) : checkpoints == null ? (
        <p className="text-sm" style={{ color: "var(--color-faint)" }}>Loading…</p>
      ) : checkpoints.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--color-faint)" }}>No checkpoints yet.</p>
      ) : (
        <ul className="flex flex-col divide-y" style={{ borderColor: "var(--color-rule)" }}>
          {sortCheckpoints(checkpoints).map((cp) => {
            const place = placeAgainstBand(cp.gut_score, slot);
            const gap = gapFromWa(cp.gut_score, slot.wa);
            return (
              <li key={cp.id} className="py-3 flex items-start gap-3" style={{ borderColor: "var(--color-rule)" }}>
                <div className="comp-tile shrink-0" style={{ minWidth: "4.5rem" }}>
                  <span className="comp-label">{cp.progress_pct}%</span>
                  <span className="comp-value">{cp.gut_score.toFixed(1)}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm" style={{ color: place ? PLACEMENT_COLOR[place] : "var(--color-muted)" }}>
                    {place ? PLACEMENT_TEXT[place] : gap != null ? "vs WA" : "logged"}
                    {gap != null && (
                      <span style={{ color: "var(--color-faint)" }}>
                        {" "}· {gap >= 0 ? "+" : ""}{gap.toFixed(2)} vs WA
                      </span>
                    )}
                  </p>
                  {cp.note && (
                    <p className="text-sm mt-0.5 whitespace-pre-wrap break-words" style={{ color: "var(--color-ink)" }}>
                      {cp.note}
                    </p>
                  )}
                  <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>
                    {new Date(cp.created_at).toLocaleDateString()}
                  </p>
                </div>
                <button
                  onClick={() => handleDelete(cp.id)}
                  className="text-xs px-2 py-1 rounded-lg shrink-0"
                  style={{ background: "var(--color-surface-2)", color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
                  aria-label={`Delete checkpoint at ${cp.progress_pct}%`}
                >
                  Delete
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
