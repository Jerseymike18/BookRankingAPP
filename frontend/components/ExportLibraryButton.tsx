"use client";

import { useState } from "react";
import type { LibraryExport } from "@/lib/types";
import { fetchMyLibraryExport, fetchUserLibraryExport } from "@/lib/api";

/**
 * "Take this library elsewhere" — downloads a Goodreads-shaped CSV that both
 * Goodreads and The StoryGraph accept on import.
 *
 * The prose here is not decoration. Three things happen to the data on the way
 * out that a reader cannot see from the file, and each is stated BEFORE the
 * download rather than discovered on the far site where nothing explains it:
 * the 0–10 score is squashed to a fifth of its resolution, the day of a read
 * date is filled in because the Ledger tracks months, and to-read books go out
 * unrated because their scores are predictions. `shelf_export.py` carries the
 * reasoning; this component carries the sentence.
 *
 * The summary is shown AFTER, for the same reason the Goodreads import shows
 * its own: a count that was dropped or left blank is not an error, but it is
 * the reader's to know.
 *
 * No new visual styles — surface/rule/sage tokens and the type scale already in
 * use on the profile pages.
 */
export default function ExportLibraryButton({
  handle,
}: {
  /** A public profile's handle, or undefined for the signed-in reader's own library. */
  handle?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LibraryExport | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onDownload() {
    setBusy(true);
    setError(null);
    try {
      const data = handle
        ? await fetchUserLibraryExport(handle)
        : await fetchMyLibraryExport();
      setResult(data);
      // A Blob + object URL rather than a link straight to the endpoint: the API
      // needs a bearer token, which a plain <a href> cannot attach. Revoked on
      // the next tick — a synchronous revoke races the download in Safari.
      const url = URL.createObjectURL(
        new Blob([data.csv], { type: "text/csv;charset=utf-8" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = data.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not build the export.");
    } finally {
      setBusy(false);
    }
  }

  const s = result?.summary;

  return (
    <div
      className="rounded-xl p-4"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <h2 className="font-display text-lg font-bold" style={{ color: "var(--color-ink)" }}>
        Export to Goodreads or StoryGraph
      </h2>
      <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
        One CSV in Goodreads&rsquo; export format — the shape both sites read on import.
        Finished books land on the <strong>read</strong> shelf with a star rating; the
        to-read queue lands on <strong>to-read</strong>.
      </p>

      <ul className="mt-3 space-y-1.5 text-xs" style={{ color: "var(--color-muted)" }}>
        <li>
          <strong style={{ color: "var(--color-ink)" }}>Stars are the score, halved.</strong>{" "}
          5★ is 9.0 and up, 4★ is 7.0–8.9, 3★ is 5.0–6.9, and so on — the same rule for
          every book, so adding one never changes a star you already exported. The exact
          score travels with each book in Goodreads&rsquo; <em>private</em> notes field,
          which is the only place four fifths of the resolution would otherwise be lost.
        </li>
        <li>
          <strong style={{ color: "var(--color-ink)" }}>Days are set to the 1st.</strong>{" "}
          The Ledger records the month a book was finished, not the day, and both sites
          want a full date. A book with only a year gets 1 January.
        </li>
        <li>
          <strong style={{ color: "var(--color-ink)" }}>To-read books go out unrated.</strong>{" "}
          They have predicted scores here, and a prediction must never arrive somewhere
          else looking like a rating that was given.
        </li>
        <li>
          No ISBNs are stored here, so both sites match on title and author. Check
          anything with a common title after importing.
        </li>
      </ul>

      <div className="flex items-center gap-3 mt-4">
        <button
          onClick={onDownload}
          disabled={busy}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{
            background: busy ? "var(--color-surface-2)" : "var(--color-sage)",
            color: busy ? "var(--color-muted)" : "#fff",
          }}
        >
          {busy ? "Building…" : "Download CSV"}
        </button>
        {s && (
          <span className="text-sm" style={{ color: "var(--color-muted)" }}>
            {s.total.toLocaleString()} book{s.total === 1 ? "" : "s"} — {s.read} read,{" "}
            {s.to_read} to-read.
          </span>
        )}
      </div>

      {/* Everything the file does NOT say for itself. Rendered only when there is
          something to report, so a clean export stays quiet. */}
      {s && (s.unrated_read > 0 || s.skipped_in_progress > 0 || s.dropped_duplicate > 0) && (
        <ul className="mt-2 space-y-1 text-xs" style={{ color: "var(--color-faint)" }}>
          {s.unrated_read > 0 && (
            <li>
              {s.unrated_read} read book{s.unrated_read === 1 ? "" : "s"} exported without a
              star — no score is on file for {s.unrated_read === 1 ? "it" : "them"}, and a
              blank means unrated rather than one star.
            </li>
          )}
          {s.skipped_in_progress > 0 && (
            <li>
              {s.skipped_in_progress} book{s.skipped_in_progress === 1 ? "" : "s"} left out
              as still being read — neither shelf is true for{" "}
              {s.skipped_in_progress === 1 ? "it" : "them"} yet.
            </li>
          )}
          {s.dropped_duplicate > 0 && (
            <li>
              {s.dropped_duplicate} duplicate title
              {s.dropped_duplicate === 1 ? "" : "s"} collapsed — both sites match on title
              and author, so the copies would have overwritten each other.
            </li>
          )}
        </ul>
      )}

      {s && s.total === 0 && (
        <p className="mt-2 text-xs" style={{ color: "var(--color-faint)" }}>
          Nothing to export yet — the file is empty apart from its header.
        </p>
      )}

      {error && (
        <p className="mt-2 text-sm" style={{ color: "#B45309" }}>
          {error}
        </p>
      )}
    </div>
  );
}
