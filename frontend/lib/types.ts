export type CategoryComponents = Record<string, Record<string, number | null>>;

export interface Book {
  rank: number;
  title: string;
  author: string;
  genre: string;
  series: string;
  words: number | null;
  year: number | null;
  year_read: number | null;
  wa: number;
  components: CategoryComponents;
  category_avgs: Record<string, number>;
}

export interface TierBook {
  title: string;
  author: string;
  genre: string;
  series: string;
  words: number | null;
  year_read: number | null;
  wa: number;
  rank: number;
  tier: string;
  components: CategoryComponents;
}

export interface TiersResponse {
  books: TierBook[];
  tier_counts: Record<string, number>;
  tier_order: string[];
  category_order: string[];
}

export interface BooksResponse {
  books: Book[];
  genres: string[];
  category_order: string[];
}

export interface BookScoresResponse {
  title: string;
  author: string;
  genre: string;
  wa: number;
  components: CategoryComponents;
}

export interface LookupResult {
  title: string;
  author: string;
  genre: string | null;
  words: number | null;
  series: string;
  blurb: string;
}

export interface InstantPrediction {
  title: string;
  author: string;
  genre: string;
  wa_final: number;
  ci: [number, number];
  rank: number;
  rank_range: [number, number];
  total: number;
  src: string;
  n_src: number;
  n_genre: number;
  wcats: Record<string, number>;
  r2: number;
}

export interface ResearchResult {
  title: string;
  author: string;
  genre: string;
  wa: number;
  ci: [number, number];
  rank: number;
  total: number;
  n_genre: number;
  n_author: number;
  conf: string;
  from_cache: boolean;
  words: number | null;
  blurb: string;
  keywords: string;
  components: CategoryComponents;
  category_order: string[];
  genre_auto_detected: boolean;
}

export interface Candidate {
  title: string;
  author: string;
  genre: string | null;
  cached: boolean;
}

export interface DiscoverCandidatesResponse {
  candidates: Candidate[];
  request: string;
}

export type ScoredCandidate = ResearchResult & { error?: string };

export interface Recommendation {
  title: string;
  author: string;
  genre: string;
  series: string;
  words: number | null;
  blurb: string;
  keywords: string;
  components: Record<string, number | null>;
  wa: number;
  predicted_rank: number;
  category_avgs: Record<string, number>;
}

export interface ReadQueueResponse {
  recommendations: Recommendation[];
  genres: string[];
}

export interface ReadingStatsSummary {
  total_books: number;
  avg_wa: number | null;
  avg_total_average: number | null;
  avg_words: number | null;
}

export interface PerYearRow {
  year: number;
  books: number;
  avg_wa: number | null;
  avg_total_average: number | null;
  avg_words: number | null;
}

export interface GenreRow {
  genre: string;
  books: number;
  avg_wa: number | null;
  avg_total_average: number | null;
  avg_words: number | null;
}

export interface AuthorRow {
  author: string;
  books: number;
  avg_wa: number | null;
}

export interface ReadingStatsResponse {
  summary: ReadingStatsSummary;
  per_year: PerYearRow[];
  by_genre: GenreRow[];
  by_author: AuthorRow[];
}

export interface UnreadBook {
  title: string;
  author: string;
  genre: string;
}

export interface FinishedBook {
  title: string;
  author: string;
  genre: string;
  year: number | null;
  wa: number;
  rank: number;
  total: number;
}

export interface ReadingStatusResponse {
  pool: UnreadBook[];
  last_year: number | null;
  finished: FinishedBook[];
}

export interface SeriesEntry {
  rank: number;
  series: string;
  author: string;
  genre: string;
  books: number;
  avg_wa: number | null;
  adjusted_wa: number | null;
  avg_total_average: number | null;
}

export interface SeriesResponse {
  series: SeriesEntry[];
}

export interface SeriesTierEntry {
  series: string;
  author: string;
  genre: string;
  books: number;
  avg_wa: number | null;
  adjusted_wa: number | null;
  avg_total_average: number | null;
  tier: string;
}

export interface SeriesTiersResponse {
  series: SeriesTierEntry[];
  tier_order: string[];
  tier_counts: Record<string, number>;
}

export interface TimelineRow {
  year: number;
  books: number;
  avg_wa: number | null;
  avg_words: number | null;
  story: number | null;
  character: number | null;
  aesthetics: number | null;
  theme: number | null;
  worldbuilding: number | null;
}

export interface TimelineResponse {
  rows: TimelineRow[];
  categories: string[];
}
