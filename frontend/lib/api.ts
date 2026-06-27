import type {
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
} from "./types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchBooks(): Promise<BooksResponse> {
  const res = await fetch(`${API}/api/books`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchValidGenres(): Promise<string[]> {
  const res = await fetch(`${API}/api/valid-genres`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchBookScores(title: string): Promise<BookScoresResponse> {
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
  words?: number;
  year_read?: number;
}

export async function addBook(payload: AddBookPayload): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${API}/api/books`, {
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
  scores: Record<string, number>
): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${API}/api/books/${encodeURIComponent(title)}/scores`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scores }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function deleteBook(title: string): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${API}/api/books/${encodeURIComponent(title)}`, {
    method: "DELETE",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function lookupBook(title: string, authorHint?: string): Promise<LookupResult> {
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
  const params = new URLSearchParams({ title, author, genre });
  const res = await fetch(`${API}/api/predict/instant?${params}`, { cache: "no-store" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function predictResearch(
  title: string,
  author: string,
  genre?: string
): Promise<ResearchResult> {
  const res = await fetch(`${API}/api/predict/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, author, genre: genre ?? null }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function discoverCandidates(
  request: string,
  maxCandidates: number
): Promise<DiscoverCandidatesResponse> {
  const res = await fetch(`${API}/api/discover/candidates`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request, max_candidates: maxCandidates }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchQueue(): Promise<string[]> {
  const res = await fetch(`${API}/api/queue`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  const data = await res.json();
  return data.titles;
}

export async function saveQueue(titles: string[]): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${API}/api/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ titles }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchTiers(year?: number): Promise<TiersResponse> {
  const params = year != null ? `?year=${year}` : "";
  const res = await fetch(`${API}/api/tiers${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchReadQueue(): Promise<ReadQueueResponse> {
  const res = await fetch(`${API}/api/read-queue`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchReadingStats(): Promise<ReadingStatsResponse> {
  const res = await fetch(`${API}/api/reading/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchReadingStatus(): Promise<ReadingStatusResponse> {
  const res = await fetch(`${API}/api/reading/status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function setYearRead(
  title: string,
  year: number
): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${API}/api/reading/set-year`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, year }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function fetchSeries(): Promise<SeriesResponse> {
  const res = await fetch(`${API}/api/series`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchSeriesTiers(): Promise<SeriesTiersResponse> {
  const res = await fetch(`${API}/api/series/tiers`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchTimeline(): Promise<TimelineResponse> {
  const res = await fetch(`${API}/api/timeline`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function generateRecommendationMeta(
  title: string,
  author: string,
  genre: string
): Promise<{ ok: boolean; blurb: string; keywords: string }> {
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
  const res = await fetch(`${API}/api/recommendations/${encodeURIComponent(title)}`, {
    method: "DELETE",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}

export async function addSeriesToQueue(seriesName: string): Promise<AddSeriesResult> {
  const res = await fetch(`${API}/api/queue/add-series`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ series_name: seriesName }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
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
}): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${API}/api/recommendations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
  return data;
}
