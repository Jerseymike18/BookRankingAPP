import type { BooksResponse, BookScoresResponse, LookupResult } from "./types";

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
