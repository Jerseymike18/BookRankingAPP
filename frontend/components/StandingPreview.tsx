"use client";

import { useEffect, useState } from "react";
import { previewBookStanding, type BookStanding } from "@/lib/api";
import { tierCircleStyle } from "@/lib/tier-tint";
import type { BookKind } from "@/lib/types";

/**
 * Live WA preview for a rating in progress — the tier-coloured circle with
 * "WA so far · would rank #n of N · X tier". The server computes everything
 * (POST /books/preview: the engine's own WA roll-up and tier banding), so the
 * number here is exactly what a save would produce. Blank components are
 * skipped, as a save rolls them up; until every required score is in, a note
 * says so. Debounced 300 ms; each change aborts the previous request.
 *
 * Shared by Add a Book and the Goodreads-import ranking card.
 */
export function StandingPreview({
  kind,
  genre,
  scores,
  required,
  title,
  onStanding,
}: {
  kind: BookKind;
  /** Fiction genre (ignored for nonfiction). */
  genre: string;
  /** Parsed scores — only the components that have a value. */
  scores: Record<string, number>;
  /** The components a save requires (drives the "so far" note). */
  required: string[];
  /** An existing title to leave out of the rank pool, if any. */
  title?: string;
  /** Told each time a new preview arrives (e.g. to tint the page's buttons). */
  onStanding?: (s: BookStanding | null) => void;
}) {
  const [preview, setPreview] = useState<BookStanding | null>(null);
  const count = Object.keys(scores).length;
  const previewGenre = kind === "fiction" ? genre : "";

  useEffect(() => {
    if (count === 0) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      previewBookStanding(kind, previewGenre, scores, ctl.signal, title)
        .then((p) => { if (!ctl.signal.aborted) { setPreview(p); onStanding?.(p); } })
        .catch(() => { /* best-effort: aborted or offline just shows nothing new */ });
    }, 300);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [kind, previewGenre, scores, count, title, onStanding]);

  if (count === 0 || !preview) return null;
  const requiredScored = required.filter((c) => scores[c] !== undefined).length;
  const partial = requiredScored < required.length;
  return (
    <div className="mt-4 flex items-center gap-3" aria-live="polite">
      <span className="wa-badge" style={tierCircleStyle(preview)}>{preview.wa.toFixed(2)}</span>
      <div className="min-w-0">
        <p className="text-sm" style={{ color: "var(--color-ink)" }}>
          {partial ? "WA so far" : "WA"} · would rank #{preview.rank} of {preview.of}
          {preview.tier ? ` · ${preview.tier} tier` : ""}
        </p>
        {partial && (
          <p className="text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
            {requiredScored} of {required.length} scored — blank components count as 0 until you fill them in
          </p>
        )}
      </div>
    </div>
  );
}
