"use client";

import { useEffect, useRef, useState } from "react";
import {
  importGoodreads,
  fetchImportStaging,
  fetchImportStatus,
  updateImportStagingRow,
  deleteImportStagingRow,
  commitImport,
  deleteImportBatch,
  addBook,
  addNonfictionBook,
} from "@/lib/api";
import type {
  ImportStagingRow,
  ImportUploadResult,
  ImportCommitResult,
  BookKind,
} from "@/lib/types";
import { componentLabel } from "@/lib/format";
import { ProgressBar } from "@/components/ProgressBar";
import { Skeleton, SkeletonText } from "@/components/Skeleton";

type Phase = "loading" | "upload" | "review" | "committed" | "rank";
type Kind = "fiction" | "nonfiction";

const SHELF_ORDER: ImportStagingRow["shelf"][] = ["to-read", "currently-reading", "read"];
const SHELF_LABEL: Record<ImportStagingRow["shelf"], string> = {
  "to-read": "To read",
  "currently-reading": "Currently reading",
  read: "Read",
};
const SHELF_NOTE: Record<ImportStagingRow["shelf"], string> = {
  "to-read": "Added to your predictions on commit.",
  "currently-reading": "Added to your predictions on commit.",
  read: "Score each to add it to your library.",
};

const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

const errorBox: React.CSSProperties = { background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" };
const cardStyle: React.CSSProperties = { background: "var(--color-surface)", border: "1px solid var(--color-rule)" };
const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)", border: "1px solid var(--color-rule)",
  color: "var(--color-ink)", fontFamily: "var(--font-body)",
};

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

/* ── Component score schema ── mirrors AddBookClient / db_write (the canonical
   source). Duplicated (not shared) so this onboarding flow stays self-contained
   and can't regress the Add-a-Book page; keep in sync if the schema changes. ── */

const COMPONENT_CATEGORIES_BY_KIND: Record<BookKind, Record<string, string[]>> = {
  fiction: {
    Story: ["Plot", "Entertainment", "Action", "Ending"],
    Character: ["Depth", "Emotional Impact", "Motivations"],
    Aesthetics: ["Prose", "Narration"],
    Theme: ["Insights", "Thought-Provokingness"],
    Worldbuilding: ["Depth2", "Integration", "Originality"],
  },
  nonfiction: {
    Substance: ["Informativeness", "Accuracy", "Originality"],
    Reasoning: ["Argumentation", "Evidence"],
    Exposition: ["Clarity", "Structure"],
    Aesthetics: ["Prose", "Voice"],
    Impact: ["Insights", "Thought-Provokingness", "Entertainment"],
  },
};
const OPTIONAL_COMPONENTS_BY_KIND: Record<BookKind, Set<string>> = {
  fiction: new Set(["Depth2", "Integration", "Originality"]),
  nonfiction: new Set(),
};
const SCORE_INPUT_RE = /^-?\d*\.?\d*$/;

function defaultScores(kind: BookKind): Record<string, string> {
  return Object.fromEntries(
    Object.values(COMPONENT_CATEGORIES_BY_KIND[kind]).flat().map((c) => [c, ""]),
  );
}
function clampScoreInput(raw: string): string {
  const t = raw.trim();
  if (t === "") return raw;
  const v = parseFloat(t);
  if (isNaN(v)) return raw;
  const c = Math.min(10, Math.max(0, v));
  return c === v ? raw : String(c);
}
function parseScores(raw: Record<string, string>): Record<string, number> {
  const parsed: Record<string, number> = {};
  for (const [comp, str] of Object.entries(raw)) {
    const t = str.trim();
    if (t === "") continue;
    const v = parseFloat(t);
    if (isNaN(v)) continue;
    parsed[comp] = Math.min(10, Math.max(0, v));
  }
  return parsed;
}

function ScoreGrid({
  categories, scores, onChange, kind,
}: {
  categories: Record<string, string[]>;
  scores: Record<string, string>;
  onChange: (comp: string, val: string) => void;
  kind: BookKind;
}) {
  return (
    <div className="space-y-5">
      {Object.entries(categories).map(([cat, comps]) => (
        <div key={cat}>
          <p className="text-xs font-semibold uppercase tracking-widest mb-2" style={{ color: "var(--color-muted)" }}>
            {cat}
          </p>
          <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))" }}>
            {comps.map((comp) => (
              <div key={comp}>
                <label className="block text-xs mb-1" style={{ color: "var(--color-muted)" }}>
                  {componentLabel(comp, kind)}
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
      ))}
    </div>
  );
}

/* ── One reviewable row ───────────────────────────────────────────────────── */

function RowCard({
  row, fictionGenres, nonfictionGenres, onKind, onGenre, onDrop,
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
  ].filter(Boolean).join(" · ");

  return (
    <div className="rounded-lg px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2" style={cardStyle}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold truncate" style={{ color: "var(--color-ink)" }}>{row.title}</span>
          {row.goodreads_rating != null && (
            <span className="shrink-0 text-xs tabular-nums" style={{ color: "var(--color-faint)" }}>
              ★ {row.goodreads_rating}/5
            </span>
          )}
        </div>
        {meta && <div className="text-xs truncate" style={{ color: "var(--color-muted)" }}>{meta}</div>}
        {row.enrich_state === "pending" && <div className="text-xs" style={{ color: "var(--color-faint)" }}>classifying…</div>}
        {row.enrich_state === "error" && (
          <div className="text-xs" style={{ color: "var(--color-spine-c)" }}>couldn&apos;t auto-classify — set kind + genre</div>
        )}
      </div>

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

      <select
        value={row.genre ?? ""}
        onChange={(e) => onGenre(row.id, e.target.value || null)}
        className="px-2 py-1.5 rounded-lg text-xs border focus:outline-none focus:ring-2 shrink-0"
        style={{ ...inputStyle, maxWidth: "12rem" }}
      >
        <option value="">— set genre —</option>
        {genres.map((g) => <option key={g} value={g}>{g}</option>)}
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

/* ── Rank one read book (mini Add-a-Book) ─────────────────────────────────── */

function RankCard({
  row, fictionGenres, nonfictionGenres, onSaved, onSkip,
}: {
  row: ImportStagingRow;
  fictionGenres: string[];
  nonfictionGenres: string[];
  onSaved: (id: string) => void;
  onSkip: (id: string) => void;
}) {
  const [kind, setKind] = useState<Kind>(row.kind ?? "fiction");
  const [genre, setGenre] = useState<string>(row.genre ?? "");
  const [year, setYear] = useState<number>(row.year_read ?? new Date().getFullYear());
  const [month, setMonth] = useState<number>(row.read_month ?? new Date().getMonth() + 1);
  const [words, setWords] = useState<number>(row.words ?? 0);
  const [scores, setScores] = useState<Record<string, string>>(defaultScores(kind));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const categories = COMPONENT_CATEGORIES_BY_KIND[kind];
  const genres = kind === "fiction" ? fictionGenres : nonfictionGenres;

  function changeKind(k: Kind) {
    setKind(k);
    setScores(defaultScores(k));
    const list = k === "fiction" ? fictionGenres : nonfictionGenres;
    setGenre(genre && list.includes(genre) ? genre : list.length === 1 ? list[0] : "");
    setError(null);
  }

  async function save() {
    const parsed = parseScores(scores);
    const required = Object.values(categories).flat().filter((c) => !OPTIONAL_COMPONENTS_BY_KIND[kind].has(c));
    const missing = required.filter((c) => parsed[c] === undefined);
    if (missing.length > 0) {
      setError(`Missing required score(s): ${missing.join(", ")}.`);
      return;
    }
    if (kind === "fiction" && !genre) {
      setError("Pick a genre first.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const common = {
        title: row.title,
        author: row.author ?? "",
        scores: parsed,
        series: row.series ?? undefined,
        series_number: row.series_number ?? undefined,
        words: words > 0 ? words : undefined,
        year_read: year,
        read_month: month,
      };
      if (kind === "nonfiction") {
        await addNonfictionBook(common);
      } else {
        await addBook({ ...common, genre });
      }
      // Added to the library — remove it from the import backlog.
      try {
        await deleteImportStagingRow(row.id);
      } catch {
        /* the book is saved; a lingering staging row is harmless */
      }
      onSaved(row.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add book.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-xl p-5" style={cardStyle}>
      <div className="flex items-baseline justify-between gap-3 mb-4">
        <h2 className="font-display font-semibold text-lg" style={{ color: "var(--color-ink)" }}>{row.title}</h2>
        {row.goodreads_rating != null && (
          <span className="shrink-0 text-xs tabular-nums" style={{ color: "var(--color-faint)" }}>
            you rated it ★ {row.goodreads_rating}/5 on Goodreads
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mb-5">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Kind</label>
          <div className="flex gap-0.5 p-0.5 rounded-lg" style={{ background: "var(--color-surface-2)" }}>
            {(["fiction", "nonfiction"] as Kind[]).map((k) => (
              <button
                key={k}
                onClick={() => changeKind(k)}
                className="flex-1 px-2 py-1 rounded-md text-xs font-medium capitalize transition-colors"
                style={{ background: kind === k ? "var(--color-surface)" : "transparent", color: kind === k ? "var(--color-sage)" : "var(--color-muted)" }}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
        {kind === "fiction" && (
          <div>
            <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Genre</label>
            <select value={genre} onChange={(e) => setGenre(e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2" style={inputStyle}>
              <option value="">— set genre —</option>
              {genres.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
        )}
        <div>
          <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Year read</label>
          <input type="number" min={1900} max={2100} step={1} value={year}
            onChange={(e) => setYear(Math.round(parseFloat(e.target.value) || 0))}
            className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2" style={inputStyle} />
        </div>
        <div>
          <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Month read</label>
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))}
            className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2" style={inputStyle}>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold uppercase tracking-widest mb-1" style={{ color: "var(--color-muted)" }}>Words (est.)</label>
          <input type="number" min={0} step={1000} value={words || ""}
            onChange={(e) => setWords(Math.round(parseFloat(e.target.value) || 0))}
            placeholder="e.g. 150000"
            className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2" style={inputStyle} />
        </div>
      </div>

      <div className="pt-4" style={{ borderTop: "1px solid var(--color-rule)" }}>
        <ScoreGrid categories={categories} scores={scores} onChange={(c, v) => setScores((p) => ({ ...p, [c]: v }))} kind={kind} />
      </div>

      {error && <div className="rounded-lg px-4 py-3 text-sm mt-4" style={errorBox}>{error}</div>}

      <div className="flex items-center gap-3 mt-5">
        <button onClick={save} disabled={saving}
          className="px-5 py-2.5 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
          style={{ background: "var(--color-sage)", color: "#fff" }}>
          {saving ? "Adding…" : "Add to library & next"}
        </button>
        <button onClick={() => onSkip(row.id)} disabled={saving}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}>
          Skip for now
        </button>
      </div>
    </section>
  );
}

/* ── Main ─────────────────────────────────────────────────────────────────── */

export default function ImportClient({
  fictionGenres, nonfictionGenres,
}: {
  fictionGenres: string[];
  nonfictionGenres: string[];
}) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ImportUploadResult | null>(null);

  const [batchId, setBatchId] = useState<string | null>(null);
  const [rows, setRows] = useState<ImportStagingRow[]>([]);
  const [enriching, setEnriching] = useState(false);
  const [enrichPending, setEnrichPending] = useState(0);
  const [enrichTotal, setEnrichTotal] = useState(0);
  const [rowError, setRowError] = useState<string | null>(null);

  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [commitResult, setCommitResult] = useState<ImportCommitResult | null>(null);

  // Ranking mode
  const [backlog, setBacklog] = useState<ImportStagingRow[]>([]);
  const [rankIndex, setRankIndex] = useState(0);

  const pollRef = useRef(0);

  // Resume on load: if the reader has staging rows from an earlier session (an
  // in-progress review, or a read-shelf ranking backlog), pick up there.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchImportStaging();
        if (cancelled) return;
        if (data.rows.length > 0) {
          setRows(data.rows);
          setBatchId(data.rows[0].batch_id ?? null);
          // Committing fans the to-read/currently-reading rows into
          // recommendations and leaves the `read` rows behind AS the ranking
          // backlog. So rows that are ALL `read` mean the commit already happened
          // and the reader was part-way through ranking — resuming them into
          // "review" dropped them on the wrong screen, showing genre/kind pickers
          // and a Commit button for books that are past both, with no way back to
          // the ranking list except pressing Commit again.
          const read = data.rows.filter((r) => r.shelf === "read");
          if (read.length === data.rows.length) {
            setBacklog(read);
            setRankIndex(0);
            setPhase("rank");
          } else {
            setPhase("review");
          }
          return;
        }
      } catch {
        /* ignore — fall through to the upload screen */
      }
      if (!cancelled) setPhase("upload");
    })();
    return () => { cancelled = true; };
  }, []);

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
        if (!pending) { setEnriching(false); return; }
      } catch {
        /* transient — keep polling */
      }
      await sleep(1500);
    }
    if (pollRef.current === myId) setEnriching(false);
  }

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
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
        // Frozen denominator for the progress bar: `pending` counts down from
        // here, so total − pending is the number actually classified so far.
        setEnrichTotal(res.staged);
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
    const genre = row.genre && list.includes(row.genre) ? row.genre : list.length === 1 ? list[0] : null;
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
    setCommitting(true);
    setCommitError(null);
    try {
      const res = await commitImport(batchId ?? undefined);
      setCommitResult(res);
      const data = await fetchImportStaging(batchId ?? undefined);
      setRows(data.rows);
      setPhase("committed");
    } catch (e) {
      setCommitError(e instanceof Error ? e.message : "Commit failed.");
    } finally {
      setCommitting(false);
    }
  }

  function startRanking() {
    const read = rows.filter((r) => r.shelf === "read");
    setBacklog(read);
    setRankIndex(0);
    setPhase("rank");
  }

  function resetToUpload() {
    pollRef.current += 1;
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
      try { await deleteImportBatch(batchId); } catch { /* ignore */ }
    }
    resetToUpload();
  }

  const committable = rows.filter((r) => r.shelf !== "read" && r.kind && r.genre).length;
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

      {/* Resume check: the client asks whether an earlier session left staging
          rows before it knows which screen to show. */}
      {phase === "loading" && (
        <div className="rounded-xl p-5" style={cardStyle}>
          <Skeleton h="0.9rem" w="12rem" className="mb-3" />
          <SkeletonText lines={3} />
        </div>
      )}

      {/* ── Upload ───────────────────────────────────────────────────────────── */}
      {phase === "upload" && (
        <section className="rounded-xl p-5" style={cardStyle}>
          <h2 className="font-display font-semibold text-base mb-2" style={{ color: "var(--color-ink)" }}>Upload your export</h2>
          <ol className="text-xs mb-4 space-y-1 list-decimal pl-4" style={{ color: "var(--color-muted)" }}>
            <li>On Goodreads: <span style={{ color: "var(--color-ink)" }}>My Books → Import/Export → Export Library</span>.</li>
            <li>Download the CSV it generates (this can take a minute).</li>
            <li>Upload it below — nothing is added to your library until you review and commit.</li>
          </ol>
          <label className="inline-flex items-center px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer transition-colors"
            style={{ background: "var(--color-sage)", color: "#fff", opacity: uploading ? 0.5 : 1 }}>
            {uploading ? "Uploading…" : "Choose CSV file"}
            <input type="file" accept=".csv,text/csv" onChange={handleFile} disabled={uploading} className="hidden" />
          </label>
          {uploading && (
            <ProgressBar
              className="mt-4"
              label="Parsing your export and staging the books…"
              hint="A large Goodreads library can take a few seconds."
            />
          )}
          {uploadError && <div className="rounded-lg px-4 py-3 text-sm mt-4" style={errorBox}>{uploadError}</div>}
        </section>
      )}

      {/* ── Review ───────────────────────────────────────────────────────────── */}
      {phase === "review" && (
        <div>
          {summary && (
            <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
              Parsed {summary.parse.kept} book{summary.parse.kept === 1 ? "" : "s"}
              {summary.skipped_existing > 0 && `, skipped ${summary.skipped_existing} already in your library`}
              {summary.parse.dropped_dupe_in_csv > 0 && `, ${summary.parse.dropped_dupe_in_csv} duplicate(s) in the file`}.
            </p>
          )}
          {enriching && (
            <div className="rounded-lg px-4 py-3 mb-4"
              style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}>
              <ProgressBar
                value={Math.max(0, enrichTotal - enrichPending)}
                max={enrichTotal || 1}
                label={
                  `Classifying fiction/nonfiction + genre — ` +
                  `${Math.max(0, enrichTotal - enrichPending)} of ${enrichTotal} done, ${enrichPending} left.`
                }
                hint="You can start reviewing now; rows fill in as they're classified."
              />
            </div>
          )}
          {rowError && <div className="rounded-lg px-4 py-3 text-sm mb-4" style={errorBox}>{rowError}</div>}

          {SHELF_ORDER.map((shelf) => {
            const group = rows.filter((r) => r.shelf === shelf);
            if (group.length === 0) return null;
            return (
              <section key={shelf} className="mb-6">
                <div className="flex items-baseline gap-2 mb-2 flex-wrap">
                  <h2 className="font-display font-semibold text-sm" style={{ color: "var(--color-ink)" }}>
                    {SHELF_LABEL[shelf]} ({group.length})
                  </h2>
                  <span className="text-xs" style={{ color: "var(--color-faint)" }}>{SHELF_NOTE[shelf]}</span>
                  {shelf === "read" && (
                    <button onClick={startRanking}
                      className="ml-auto px-3 py-1 rounded-lg text-xs font-semibold transition-colors"
                      style={{ background: "var(--color-sage)", color: "#fff" }}>
                      Rank these →
                    </button>
                  )}
                </div>
                <div className="space-y-2">
                  {group.map((row) => (
                    <RowCard key={row.id} row={row} fictionGenres={fictionGenres} nonfictionGenres={nonfictionGenres}
                      onKind={onKind} onGenre={onGenre} onDrop={onDrop} />
                  ))}
                </div>
              </section>
            );
          })}

          {commitError && <div className="rounded-lg px-4 py-3 text-sm mb-4" style={errorBox}>{commitError}</div>}

          <div className="flex flex-wrap items-center gap-3 mt-6">
            <button onClick={commit} disabled={committing || committable === 0}
              className="px-6 py-3 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
              style={{ background: "var(--color-sage)", color: "#fff" }}>
              {committing ? "Committing…" : `Commit — add ${committable} to your predictions`}
            </button>
            <span className="text-xs" style={{ color: "var(--color-muted)" }}>
              {readCount > 0 && `${readCount} read book${readCount === 1 ? "" : "s"} to rank. `}
              Rows missing a kind or genre are skipped and kept for you to fix.
            </span>
            <button onClick={startOver}
              className="ml-auto px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}>
              Discard import
            </button>
          </div>
          {committing && (
            // One server-side call fans out over every committable row, so the
            // client sees no intermediate count — indeterminate is the truth.
            <ProgressBar
              className="mt-3"
              label={`Adding ${committable} book${committable === 1 ? "" : "s"} to your predictions…`}
              hint="Don't close this tab — the rows are written in one pass."
            />
          )}
        </div>
      )}

      {/* ── Committed ────────────────────────────────────────────────────────── */}
      {phase === "committed" && commitResult && (
        <div>
          <div className="rounded-lg px-4 py-3 text-sm mb-4"
            style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" }}>
            Added {commitResult.committed} book{commitResult.committed === 1 ? "" : "s"} to your predictions.
          </div>

          {commitResult.backlog > 0 && (
            <div className="rounded-lg px-4 py-4 mb-4" style={cardStyle}>
              <div className="text-sm mb-3">
                <span style={{ color: "var(--color-ink)" }}>
                  {commitResult.backlog} read book{commitResult.backlog === 1 ? "" : "s"} ready to rank.
                </span>{" "}
                <span style={{ color: "var(--color-muted)" }}>Score each to add it to your library.</span>
              </div>
              <button onClick={startRanking}
                className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}>
                Rank your read books →
              </button>
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
                    <span className="truncate" style={{ color: "var(--color-ink)" }}>{s.title}</span>
                    <span className="shrink-0" style={{ color: "var(--color-muted)" }}>{s.reason}</span>
                  </li>
                ))}
              </ul>
              <button onClick={() => setPhase("review")}
                className="mt-3 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}>
                Back to review to fix these
              </button>
            </div>
          )}

          <button onClick={resetToUpload}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}>
            Import another file
          </button>
        </div>
      )}

      {/* ── Rank the read backlog ────────────────────────────────────────────── */}
      {phase === "rank" && (
        <div>
          {rankIndex >= backlog.length ? (
            <div>
              <div className="rounded-lg px-4 py-3 text-sm mb-4"
                style={{ background: "var(--color-sage-light)", color: "var(--color-sage)", border: "1px solid var(--color-sage)" }}>
                All caught up — no more read books to rank.
              </div>
              <button onClick={() => setPhase(commitResult ? "committed" : "review")}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{ color: "var(--color-muted)", border: "1px solid var(--color-rule)" }}>
                ← Back
              </button>
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
                  Ranking {rankIndex + 1} of {backlog.length}
                </p>
                <button onClick={() => setPhase(commitResult ? "committed" : "review")}
                  className="text-xs font-medium transition-colors" style={{ color: "var(--color-muted)" }}>
                  Save &amp; finish later
                </button>
              </div>
              <RankCard
                key={backlog[rankIndex].id}
                row={backlog[rankIndex]}
                fictionGenres={fictionGenres}
                nonfictionGenres={nonfictionGenres}
                onSaved={(id) => { setRows((rs) => rs.filter((r) => r.id !== id)); setRankIndex((i) => i + 1); }}
                onSkip={() => setRankIndex((i) => i + 1)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
