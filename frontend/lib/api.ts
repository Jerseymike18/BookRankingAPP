import type {
  CalibrationHealth,
  LooResult,
  ResearcherComparison,
  DeltaLogResponse,
  BooksResponse,
  BookScoresResponse,
  LookupResult,
  InstantPrediction,
  ResearchResult,
  DiscoverCandidatesResponse,
  ReadQueueResponse,
  TiersResponse,
  ReadingStatsResponse,
  ReadingStatusResponse,
  SeriesResponse,
  SeriesTiersResponse,
  TimelineResponse,
  AddSeriesResult,
  BookKind,
  CombinedStatsResponse,
  RepredictHandle,
  RepredictPoll,
} from "./types";
import { slugify } from "./slug";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Static (backend-free) deployment: read snapshots from /data/*.json instead
 * of hitting the FastAPI server. Set at build time on Vercel. See
 * scripts/export_static_data.py for how the snapshots are produced. */
const STATIC = process.env.NEXT_PUBLIC_STATIC_DATA === "1";

/** API path prefix for a library: fiction → /api, nonfiction → /api/nonfiction. */
function base(kind: BookKind = "fiction"): string {
  return `${API}${kind === "nonfiction" ? "/api/nonfiction" : "/api"}`;
}

/** Read one snapshot file from frontend/public/data/. Works in both contexts:
 * server components (build/SSR — no origin available, so read from disk) and
 * the browser (fetch the static asset). The variable specifier + ignore
 * comments keep the Node builtins out of the client bundle. */
async function getJSON<T>(file: string): Promise<T> {
  if (typeof window === "undefined") {
    const fsMod = "node:fs/promises";
    const pathMod = "node:path";
    const fs = await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ fsMod);
    const path = await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ pathMod);
    const p = path.join(process.cwd(), "public", "data", file);
    return JSON.parse(await fs.readFile(p, "utf8")) as T;
  }
  const res = await fetch(`/data/${file}`);
  if (!res.ok) throw new Error(`Static data ${file}: ${res.status}`);
  return res.json() as Promise<T>;
}

/** Guard for write/compute functions: hard-fails in a read-only static build so
 * a stray mutation call surfaces immediately instead of silently 404ing. */
function assertWritable(): void {
  if (STATIC) throw new Error("Read-only deployment");
}

export async function fetchBooks(kind: BookKind = "fiction"): Promise<BooksResponse> {
  if (STATIC) return getJSON<BooksResponse>(`${kind}/books.json`);
  const res = await fetch(`${base(kind)}/books`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchValidGenres(kind: BookKind = "fiction"): Promise<string[]> {
  if (STATIC) return getJSON<string[]>(`${kind}/valid-genres.json`);
  const res = await fetch(`${base(kind)}/valid-genres`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/** Partial metadata update for an already-ranked book. Only the keys present
 * are changed (omit-unchanged); `title` is a rename (cascaded server-side). */
export interface BookMetadataPayload {
  title?: string;
  author?: string;
  genre?: string;
  series?: string;
  series_number?: number;
  words?: number;
  year_read?: number;
}

export async function updateBookMetadata(
  currentTitle: string,
  payload: BookMetadataPayload,
  kind: BookKind = "fiction"
): Promise<{ ok: boolean; message: string; renamed_to: string | null; cascade: Record<string, number> }> {
  assertWritable();
  const res = await fetch(
    `${base(kind)}/books/${encodeURIComponent(currentTitle)}/metadata`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchBookScores(title: string): Promise<BookScoresResponse> {
  if (STATIC) return getJSON<BookScoresResponse>(`fiction/scores/${slugify(title)}.json`);
  const res = await fetch(
    `${API}/api/books/${encodeURIComponent(title)}/scores`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export interface AddBookPayload {
  title: string;
  genre: string;
  author: string;
  scores: Record<string, number>;
  series?: string;
  series_number?: number;
  words?: number;
  year_read?: number;
}

export interface AddBookResult {
  ok: boolean;
  message: string;
  // Present for fiction adds: a handle for the background cohort re-prediction.
  repredict?: RepredictHandle | null;
}

export async function addBook(payload: AddBookPayload): Promise<AddBookResult> {
  assertWritable();
  const res = await fetch(`${API}/api/books`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

/** Poll for a background cohort re-prediction's report by its token. Resolves to
 *  {status:"pending"} until the background pass finishes, then {status:"done"}. */
export async function fetchRepredictRecent(token: string): Promise<RepredictPoll> {
  const res = await fetch(
    `${API}/api/repredict/recent?token=${encodeURIComponent(token)}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export interface AddNonfictionBookPayload {
  title: string;
  author?: string;
  genre?: string;
  scores: Record<string, number>;
  series?: string;
  series_number?: number;
  words?: number;
  year_read?: number;
}

export async function addNonfictionBook(
  payload: AddNonfictionBookPayload
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/books`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function editRating(
  title: string,
  scores: Record<string, number>,
  kind: BookKind = "fiction"
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base(kind)}/books/${encodeURIComponent(title)}/scores`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scores }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function deleteBook(
  title: string,
  kind: BookKind = "fiction"
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base(kind)}/books/${encodeURIComponent(title)}`, {
    method: "DELETE",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function lookupBook(title: string, authorHint?: string): Promise<LookupResult> {
  assertWritable();
  const res = await fetch(`${API}/api/lookup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, author_hint: authorHint ?? "" }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function predictInstant(
  title: string,
  author: string,
  genre: string
): Promise<InstantPrediction> {
  assertWritable();
  const params = new URLSearchParams({ title, author, genre });
  const res = await fetch(`${API}/api/predict/instant?${params}`, { cache: "no-store" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function predictResearch(
  title: string,
  author: string,
  genre?: string,
  grounded = false
): Promise<ResearchResult> {
  assertWritable();
  const res = await fetch(`${API}/api/predict/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, author, genre: genre ?? null, grounded }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function predictNonfiction(
  title: string,
  author: string,
  genre?: string
): Promise<import("./types").NonfictionPrediction> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/predict/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, author, genre: genre ?? null }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function discoverNonfictionCandidates(
  request: string,
  n?: number
): Promise<import("./types").NonfictionDiscoverResponse> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/discover/candidates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request, n: n ?? null }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function discoverCandidates(
  request: string,
  maxCandidates?: number
): Promise<DiscoverCandidatesResponse> {
  assertWritable();
  const res = await fetch(`${API}/api/discover/candidates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request,
      max_candidates: maxCandidates ?? null,
    }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchQueue(): Promise<string[]> {
  if (STATIC) return (await getJSON<{ titles: string[] }>("fiction/queue.json")).titles;
  const res = await fetch(`${API}/api/queue`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.titles;
}

export async function saveQueue(titles: string[]): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${API}/api/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchTiers(year?: number, kind: BookKind = "fiction"): Promise<TiersResponse> {
  if (STATIC) {
    // Nonfiction has no year_read (endpoint ignores the param), so it always
    // maps to the single file; fiction has a snapshot per year read.
    if (kind === "fiction" && year != null) return getJSON<TiersResponse>(`fiction/tiers-${year}.json`);
    return getJSON<TiersResponse>(`${kind}/tiers.json`);
  }
  const params = kind === "fiction" && year != null ? `?year=${year}` : "";
  const res = await fetch(`${base(kind)}/tiers${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchReadQueue(): Promise<ReadQueueResponse> {
  if (STATIC) return getJSON<ReadQueueResponse>("fiction/read-queue.json");
  const res = await fetch(`${API}/api/read-queue`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

// ── Nonfiction TBR (recommendations + read queue) ──
export async function fetchNonfictionReadQueue(): Promise<import("./types").NonfictionReadQueueResponse> {
  if (STATIC) return getJSON<import("./types").NonfictionReadQueueResponse>("nonfiction/read-queue.json");
  const res = await fetch(`${base("nonfiction")}/read-queue`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export interface SaveNonfictionRecPayload {
  title: string;
  author?: string;
  genre?: string;
  scores: Record<string, number>;
  series?: string;
  series_number?: number;
  words?: number;
  blurb?: string;
  keywords?: string;
}

export async function saveNonfictionRecommendation(
  payload: SaveNonfictionRecPayload
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/recommendations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function deleteNonfictionRecommendation(
  title: string
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/recommendations/${encodeURIComponent(title)}`, {
    method: "DELETE",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function setNonfictionDone(
  title: string,
  done = true
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/recommendations/${encodeURIComponent(title)}/done`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchNonfictionQueue(): Promise<string[]> {
  if (STATIC) return (await getJSON<{ titles: string[] }>("nonfiction/queue.json")).titles;
  const res = await fetch(`${base("nonfiction")}/queue`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()).titles;
}

export async function saveNonfictionQueue(
  titles: string[]
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base("nonfiction")}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchReadingStats(kind: BookKind = "fiction"): Promise<ReadingStatsResponse> {
  if (STATIC) return getJSON<ReadingStatsResponse>(`${kind}/reading-stats.json`);
  const res = await fetch(`${base(kind)}/reading/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchReadingStatus(kind: BookKind = "fiction"): Promise<ReadingStatusResponse> {
  if (STATIC) return getJSON<ReadingStatusResponse>(`${kind}/reading-status.json`);
  const res = await fetch(`${base(kind)}/reading/status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function setYearRead(
  title: string,
  year: number,
  kind: BookKind = "fiction"
): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${base(kind)}/reading/set-year`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, year }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchSeries(kind: BookKind = "fiction"): Promise<SeriesResponse> {
  if (STATIC) return getJSON<SeriesResponse>(`${kind}/series.json`);
  const res = await fetch(`${base(kind)}/series`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchSeriesTiers(kind: BookKind = "fiction"): Promise<SeriesTiersResponse> {
  if (STATIC) return getJSON<SeriesTiersResponse>(`${kind}/series-tiers.json`);
  const res = await fetch(`${base(kind)}/series/tiers`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchTimeline(kind: BookKind = "fiction"): Promise<TimelineResponse> {
  if (STATIC) return getJSON<TimelineResponse>(`${kind}/timeline.json`);
  const res = await fetch(`${base(kind)}/timeline`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function generateRecommendationMeta(
  title: string,
  author: string,
  genre: string
): Promise<{ ok: boolean; blurb: string; keywords: string }> {
  assertWritable();
  const res = await fetch(`${API}/api/recommendations/generate-meta`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, author, genre }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function deleteRecommendation(title: string): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${API}/api/recommendations/${encodeURIComponent(title)}`, {
    method: "DELETE",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function addSeriesToQueue(seriesName: string): Promise<AddSeriesResult> {
  assertWritable();
  const res = await fetch(`${API}/api/queue/add-series`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ series_name: seriesName }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchCalibrationHealth(): Promise<CalibrationHealth> {
  if (STATIC) return getJSON<CalibrationHealth>("calibration-health.json");
  const res = await fetch(`${API}/api/calibration/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function runLooValidation(): Promise<LooResult> {
  assertWritable();
  const res = await fetch(`${API}/api/calibration/loo`, { method: "POST" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

/** Last memory-vs-web-grounded comparison, or null if none has been run. */
export async function fetchResearcherComparison(): Promise<ResearcherComparison | null> {
  // The snapshot holds either the comparison object or JSON null (no run yet).
  if (STATIC) return getJSON<ResearcherComparison | null>("calibration-researcher-comparison.json");
  const res = await fetch(`${API}/api/calibration/researcher-comparison`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<CombinedStatsResponse> {
  if (STATIC) return getJSON<CombinedStatsResponse>("stats.json");
  const res = await fetch(`${API}/api/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchDeltaLog(): Promise<DeltaLogResponse> {
  if (STATIC) return getJSON<DeltaLogResponse>("delta-log.json");
  const res = await fetch(`${API}/api/delta-log`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function saveRecommendation(payload: {
  title: string;
  genre: string;
  author: string;
  scores: Record<string, number>;
  words?: number | null;
  blurb?: string;
  keywords?: string;
  series?: string;
  series_number?: number;
}): Promise<{ ok: boolean; message: string }> {
  assertWritable();
  const res = await fetch(`${API}/api/recommendations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}
