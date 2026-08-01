"use client";

import { useRef, useState } from "react";
import {
  importGoodreads,
  fetchImportStaging,
  fetchImportStatus,
  updateImportStagingRow,
  deleteImportStagingRow,
  commitImport,
  deleteImportBatch,
} from "@/lib/api";
import type {
  ImportStagingRow,
  ImportUploadResult,
  ImportCommitResult,
} from "@/lib/types";

type Phase = "upload" | "review" | "committed";
type Kind = "fiction" | "nonfiction";

const SHELF_ORDER: ImportStagingRow["shelf"][] = [
  "to-read",
  "currently-reading",
  "read",
];
const SHELF_LABEL: Record<ImportStagingRow["shelf"], string> = {
  "to-read": "To read",
  "currently-reading": "Currently reading",
  read: "Read",
};
const SHELF_NOTE: Record<ImportStagingRow["shelf"], string> = {
  "to-read": "Added to your predictions on commit.",
  "currently-reading": "Added to your predictions on commit.",
  read: "Saved as a ranking backlog — you'll score each to add it to your library.",
};

const errorBox: React.CSSProperties = {
  background: "#FEF2F2",
  color: "#B91C1C",
  border: "1px solid #FCA5A5",
};
const cardStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
};
const selectStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

/* ── One reviewable row ───────────────────────────────────────────────────── */

function RowCard({
  row,
  fictionGenres,
  nonfictionGenres,
  onKind,
  onGenre,
  onDrop,
}: {
  row: ImportStagingRow;
  fictionGenres: string[];
  nonfictionGenres: string[];
  onKind: (id: string, kind: Kind) => void;
  onGenre: (id: string, genre: string | null) => void;
  onDrop: (id: string) => void;
}) {
  const genres = row.kind === "nonfiction" ? nonfictionGenres : fictionGenres;
  const meta = [
    row.author,
    row.series ? `${row.series}${row.series_number ? ` #${row.series_number}` : ""}` : null,
    row.words ? `~${row.words.toLocaleString()} words` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="rounded-lg px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2" style={cardStyle}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold truncate" style={{ color: "var(--color-ink)" }}>
            {row.title}
          </span>
          {row.goodreads_rating != null && (
            <span className="shrink-0 text-xs tabular-nums" style={{ color: "var(--color-faint)" }}>
              ★ {row.goodreads_rating}/5
            </span>
          )}
        </div>
        {meta && (
          <div className="text-xs truncate" style={{ color: "var(--color-muted)" }}>
            {meta}
          </div>
        )}
        {row.enrich_state === "pending" && (
          <div className="text-xs" style={{ color: "var(--color-faint)" }}>
            classifying…
          </div>
        )}
        {row.enrich_state === "error" && (
          <div className="text-xs" style={{ color: "var(--color-spine-c)" }}>
            couldn&apos;t auto-classify — set kind + genre below
          </div>
        )}
      </div>

      {/* Fiction / Nonfiction toggle */}
      <div className="flex gap-0.5 p-0.5 rounded-lg shrink-0" style={{ background: "var(--color-surface-2)" }}>
        {(["fiction", "nonfiction"] as Kind[]).map((k) => (
          <button
            key={k}
            onClick={() => onKind(row.id, k)}
            className="px-2.5 py-1 rounded-md text-xs font-medium capitalize transition-colors"
            style={{
              background: row.kind === k ? "var(--color-surface)" : "transparent",
              color: row.kind === k ? "var(--color-sage)" : "var(--color-muted)",
            }}
          >
            {k}
          </button>
        ))}
      </div>

      {/* Genre */}
      <select
        value={row.genre ?? ""}
        onChange={(e) => onGenre(row.id, e.target.value || null)}
        className="px-2 py-1.5 rounded-lg text-xs border focus:outline-none focus:ring-2 shrink-0"
        style={{ ...selectStyle, maxWidth: "12rem" }}
      >
        <option value="">— set genre —</option>
        {genres.map((g) => (
          <option key={g} value={g}>
            {g}
          </option>
        ))}
      </select>

      <button
        onClick={() => onDrop(row.id)}
        aria-label="Remove from import"
        className="shrink-0 px-2 py-1 rounded-md text-xs font-medium transition-colors"
        style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
      >
        ✗
      </button>
    </div>
  );
}

/* ── Main ─────────────────────────────────────────────────────────────────── */

export default function ImportClient({
  fictionGenres,
  nonfictionGenres,
}: {
  fictionGenres: string[];
  nonfictionGenres: string[];
}) {
  const [phase, setPhase] = useState<Phase>("upload");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ImportUploadResult | null>(null);

  const [batchId, setBatchId] = useState<string | null>(null);
  const [rows, setRows] = useState<ImportStagingRow[]>([]);
  const [enriching, setEnriching] = useState(false);
  const [enrichPending, setEnrichPending] = useState(0);
  const [rowError, setRowError] = useState<string | null>(null);

  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [commitResult, setCommitResult] = useState<ImportCommitResult | null>(null);

  const pollRef = useRef(0);

  async function pollEnrichment(bid: string) {
    const myId = ++pollRef.current;
    for (let i = 0; i < 40; i++) {
      if (pollRef.current !== myId) return;
      try {
        const st = await fetchImportStatus(bid);
        const data = await fetchImportStaging(bid);
        if (pollRef.current !== myId) return;
        setRows(data.rows);
        const pending = st.by_enrich?.pending ?? 0;
        setEnrichPending(pending);
        if (!pending) {
          setEnriching(false);
          return;
        }
      } catch {
        /* transient — keep polling */
      }
      await sleep(1500);
    }
    if (pollRef.current === myId) setEnriching(false); // timed out; stop the spinner
  }

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const text = await file.text();
      const res = await importGoodreads(text, file.name);
      setSummary(res);
      setBatchId(res.batch_id);
      const data = await fetchImportStaging(res.batch_id);
      setRows(data.rows);
      setPhase("review");
      if (res.enriching) {
        setEnriching(true);
        setEnrichPending(res.staged);
        void pollEnrichment(res.batch_id);
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  function patchLocal(id: string, patch: Partial<ImportStagingRow>) {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  async function onGenre(id: string, genre: string | null) {
    const prev = rows.find((r) => r.id === id)?.genre ?? null;
    patchLocal(id, { genre });
    try {
      await updateImportStagingRow(id, { genre });
    } catch (e) {
      patchLocal(id, { genre: prev });
      setRowError(e instanceof Error ? e.message : "Could not save genre.");
    }
  }

  async function onKind(id: string, kind: Kind) {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    const list = kind === "fiction" ? fictionGenres : nonfictionGenres;
    // Keep the genre if it's valid for the new kind; auto-pick when unambiguous
    // (nonfiction has one genre); otherwise clear so the user chooses.
    const genre =
      row.genre && list.includes(row.genre)
        ? row.genre
        : list.length === 1
          ? list[0]
          : null;
    const prev = { kind: row.kind, genre: row.genre };
    patchLocal(id, { kind, genre });
    try {
      await updateImportStagingRow(id, { kind, genre });
    } catch (e) {
      patchLocal(id, prev);
      setRowError(e instanceof Error ? e.message : "Could not save.");
    }
  }

  async function onDrop(id: string) {
    const prev = rows;
    setRows((rs) => rs.filter((r) => r.id !== id));
    try {
      await deleteImportStagingRow(id);
    } catch (e) {
      setRows(prev);
      setRowError(e instanceof Error ? e.message : "Could not remove.");
    }
  }

  async function commit() {
    if (!batchId) return;
    setCommitting(true);
    setCommitError(null);
    try {
      const res = await commitImport(batchId);
      setCommitResult(res);
      const data = await fetchImportStaging(batchId);
      setRows(data.rows);
      setPhase("committed");
    } catch (e) {
      setCommitError(e instanceof Error ? e.message : "Commit failed.");
    } finally {
      setCommitting(false);
    }
  }

  function resetToUpload() {
    pollRef.current += 1; // cancel any in-flight poll
    setPhase("upload");
    setRows([]);
    setBatchId(null);
    setSummary(null);
    setCommitResult(null);
    setUploadError(null);
    setRowError(null);
    setEnriching(false);
  }

  async function startOver() {
    if (batchId) {
      try {
        await deleteImportBatch(batchId);
      } catch {
        /* ignore — resetting regardless */
      }
    }
    resetToUpload();
  }

  const committable = rows.filter(
    (r) => r.shelf !== "read" && r.kind && r.genre,
  ).length;
  const readCount = rows.filter((r) => r.shelf === "read").length;

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Import from Goodreads
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          Upload your Goodreads library export so you only have to rank your books —
          not re-enter every one&apos;s metadata.
        </p>
      </div>

      {/* ── Upload ───────────────────────────────────────────────────────────── */}
      {phase === "upload" && (
        <section className="rounded-xl p-5" style={cardStyle}>
          <h2 className="font-display font-semibold text-base mb-2" style={{ color: "var(--color-ink)" }}>
            Upload your export
          </h2>
          <ol className="text-xs mb-4 space-y-1 list-decimal pl-4" style={{ color: "var(--color-muted)" }}>
            <li>
              On Goodreads: <span style={{ color: "var(--color-ink)" }}>My Books → Import/Export → Export Library</span>.
            </li>
            <li>Download the CSV it generates (this can take a minute).</li>
            <li>Upload it below — nothing is added to your library until you review and commit.</li>
          </ol>

          <label
            className="inline-flex items-center px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer transition-colors"
            style={{ background: "var(--color-sage)", color: "#fff", opacity: uploading ? 0.5 : 1 }}
          >
            {uploading ? "Uploading…" : "Choose CSV file"}
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={handleFile}
              disabled={uploading}
              className="hidden"
            />
          </label>

          {uploadError && (
            <div className="rounded-lg px-4 py-3 text-sm mt-4" style={errorBox}>
              {uploadError}
            </div>
          )}
        </section>
      )}

      {/* ── Review ───────────────────────────────────────────────────────────── */}
      {phase === "review" && (
        <div>
          {summary && (
            <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
              Parsed {summary.parse.kept} book{summary.parse.kept === 1 ? "" : "s"}
              {summary.skipped_existing > 0 && `, skipped ${summary.skipped_existing} already in your library`}
              {summary.parse.dropped_dupe_in_csv > 0 && `, ${summary.parse.dropped_dupe_in_csv} duplicate(s) in the file`}
              .
            </p>
          )}

          {enriching && (
            <div
              className="rounded-lg px-4 py-3 text-sm mb-4"
              style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)", color: "var(--color-muted)" }}
            >
              Classifying fiction/nonfiction + genre… {enrichPending} left. You can start reviewing now.
            </div>
          )}

          {rowError && (
            <div className="rounded-lg px-4 py-3 text-sm mb-4" style={errorBox}>
              {rowError}
            </div>
          )}

          {SHELF_ORDER.map((shelf) => {
            const group = rows.filter((r) => r.shelf === shelf);
            if (group.length === 0) return null;
            return (
              <section key={shelf} className="mb-6">
                <div className="flex items-baseline gap-2 mb-2">
                  <h2 className="font-display font-semibold text-sm" style={{ color: "var(--color-ink)" }}>
                    {SHELF_LABEL[shelf]} ({group.length})
                  </h2>
                  <span className="text-xs" style={{ color: "var(--color-faint)" }}>
                    {SHELF_NOTE[shelf]}
                  </span>
                </div>
                <div className="space-y-2">
                  {group.map((row) => (
                    <RowCard
                      key={row.id}
                      row={row}
                      fictionGenres={fictionGenres}
                      nonfictionGenres={nonfictionGenres}
                      onKind={onKind}
                      onGenre={onGenre}
                      onDrop={onDrop}
                    />
                  ))}
                </div>
              </section>
            );
          })}

          {commitError && (
            <div className="rounded-lg px-4 py-3 text-sm mb-4" style={errorBox}>
              {commitError}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 mt-6">
            <button
              onClick={commit}
              disabled={committing || committable === 0}
              className="px-6 py-3 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-sage)", color: "#fff" }}
            >
              {committing ? "Committing…" : `Commit — add ${committable} to your predictions`}
            </button>
            <span className="text-xs" style={{ color: "var(--color-muted)" }}>
              {readCount > 0 && `${readCount} read book${readCount === 1 ? "" : "s"} will wait in your ranking backlog. `}
              Rows missing a kind or genre are skipped and kept for you to fix.
            </span>
            <button
              onClick={startOver}
              className="ml-auto px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
            >
              Discard import
            </button>
          </div>
        </div>
      )}

      {/* ── Committed ────────────────────────────────────────────────────────── */}
      {phase === "committed" && commitResult && (
        <div>
          <div
            className="rounded-lg px-4 py-3 text-sm mb-4"
            style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" }}
          >
            Added {commitResult.committed} book{commitResult.committed === 1 ? "" : "s"} to your
            predictions.
          </div>

          {commitResult.backlog > 0 && (
            <div className="rounded-lg px-4 py-3 text-sm mb-4" style={cardStyle}>
              <span style={{ color: "var(--color-ink)" }}>
                {commitResult.backlog} read book{commitResult.backlog === 1 ? "" : "s"} saved to your
                ranking backlog.
              </span>{" "}
              <span style={{ color: "var(--color-muted)" }}>
                You&apos;ll score each one to add it to your library (ranking is coming next).
              </span>
            </div>
          )}

          {commitResult.skipped.length > 0 && (
            <div className="rounded-lg px-4 py-3 mb-4" style={cardStyle}>
              <div className="text-sm font-semibold mb-2" style={{ color: "var(--color-ink)" }}>
                {commitResult.skipped.length} couldn&apos;t be added yet
              </div>
              <ul className="space-y-1">
                {commitResult.skipped.map((s) => (
                  <li key={s.id} className="text-xs flex justify-between gap-3">
                    <span className="truncate" style={{ color: "var(--color-ink)" }}>
                      {s.title}
                    </span>
                    <span className="shrink-0" style={{ color: "var(--color-muted)" }}>
                      {s.reason}
                    </span>
                  </li>
                ))}
              </ul>
              <button
                onClick={() => setPhase("review")}
                className="mt-3 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                Back to review to fix these
              </button>
            </div>
          )}

          <button
            onClick={resetToUpload}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}
          >
            Import another file
          </button>
        </div>
      )}
    </div>
  );
}
