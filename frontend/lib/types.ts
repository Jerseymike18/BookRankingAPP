export type CategoryComponents = Record<string, Record<string, number | null>>;

/** Which library a view is scoped to. Drives the API prefix and the primary
 *  ranking score (fiction → WA, nonfiction → Total Average). */
export type BookKind = "fiction" | "nonfiction";

export interface Book {
  rank: number;
  title: string;
  author: string;
  genre: string;
  series: string;
  series_number: number | null;
  words: number | null;
  year: number | null;
  year_read: number | null;
  wa: number;
  /** Present for nonfiction (its primary ranking score); absent for fiction. */
  total_average?: number | null;
  components: CategoryComponents;
  category_avgs: Record<string, number>;
}

export interface TierBook {
  title: string;
  author: string;
  genre: string;
  series: string;
  series_number: number | null;
  words: number | null;
  year_read: number | null;
  wa: number;
  total_average?: number | null;
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
  series_number: number | null;
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
  wa_model: number;
  bias: number;
  trust: number;
  analog_mean: number;
  r2: number;
  resid_sd: number;
  est: Record<string, number>;
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
  series: string;
  series_number: number | null;
  blurb: string;
  keywords: string;
  components: CategoryComponents;
  category_order: string[];
  genre_auto_detected: boolean;
}

export interface NonfictionPrediction {
  title: string;
  author: string;
  genre: string;
  components: CategoryComponents;
  category_avgs: Record<string, number>;
  wa: number;
  total_average: number;
  rank: number;
  total: number;
  confidence: string;
  low_confidence: boolean;
  category_order: string[];
}

export interface NonfictionCandidate {
  title: string;
  author: string;
}

export interface NonfictionDiscoverResponse {
  candidates: NonfictionCandidate[];
  request: string;
  note?: string;
}

export interface Candidate {
  title: string;
  author: string;
  genre: string | null;
  cached: boolean;
  /** Goodreads canonical series name (series-enumeration requests only). */
  series?: string | null;
  /** Goodreads ordinal — e.g. 1, or 0.5 for a novella. Null when standalone/unknown. */
  series_number?: number | null;
}

export interface DiscoverCandidatesResponse {
  candidates: Candidate[];
  request: string;
  /** Non-empty when fewer than requested could be found (UI shows the reason). */
  note?: string;
  /** Goodreads URLs the series list was extracted from (provenance). */
  sources?: string[];
}

export type ScoredCandidate = ResearchResult & { error?: string };

export interface Recommendation {
  title: string;
  author: string;
  genre: string;
  series: string;
  series_number: number | null;
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

export interface NonfictionRecommendation {
  title: string;
  author: string;
  genre: string;
  series: string;
  series_number: number | null;
  words: number | null;
  blurb: string;
  keywords: string;
  components: Record<string, number | null>;
  category_avgs: Record<string, number>;
  wa: number | null;
  total_average: number | null;
  predicted_rank: number | null;
}

export interface NonfictionReadQueueResponse {
  recommendations: NonfictionRecommendation[];
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

export interface StatusSlot {
  title: string;
  author: string;
  genre: string;
  series: string;
  series_number: number | null;
  has_prediction: boolean;
  wa: number | null;
  rank: number | null;
  total: number;
  category_avgs: Record<string, number>;
}

export interface ReadingStatusResponse {
  last_read: StatusSlot | null;
  currently_reading: StatusSlot | null;
  reading_next: StatusSlot | null;
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
  // Category averages keyed by lowercased category name. Fiction: story /
  // character / aesthetics / theme / worldbuilding. Nonfiction: quality /
  // aesthetics / theme. Index signature so either set is valid.
  [cat: string]: number | null;
}

export interface TimelineResponse {
  rows: TimelineRow[];
  categories: string[];
}

export interface DeltaLogEntry {
  id: number;
  title: string;
  logged_at: string;
  pred_wa: number | null;
  act_wa: number | null;
  d_wa: number | null;
  [key: string]: number | string | null;  // pred_*/act_*/d_* component columns
}

export interface DeltaLogResponse {
  entries: DeltaLogEntry[];
  components: string[];
  drift: Record<string, number | null>;
}

export interface CalibrationHealth {
  n_books: number;
  r2: number;
  resid_sd: number;
  coeffs: {
    intercept: number;
    story: number;
    character: number;
    aesthetics: number;
    theme: number;
  };
  genre_info: Record<string, { bias: number; n: number; trust: number }>;
}

export interface LooGenreRow {
  genre: string;
  n: number;
  mae: number;
  verdict: string;
}

export interface LooComponentRow {
  component: string;
  mae: number;
  n: number;
  verdict: string;
}

export interface LooResult {
  n_books: number;
  naive_mae: number;
  engine_mae: number;
  within_0_5: number;
  within_1_0: number;
  improvement_pct: number;
  bias_mae: number;
  no_bias_mae: number;
  bias_helps: boolean;
  bias_delta: number;
  per_genre: LooGenreRow[];
  per_component: LooComponentRow[];
}

export interface ResearcherComponentRow {
  component: string;
  n: number;
  memory_mae: number;
  grounded_mae: number;
  delta: number; // memory_mae - grounded_mae; positive = grounding lowers error
  verdict: string; // "grounding helps" | "no change" | "grounding HURTS"
  loo_mae: number | null;
  signal: string | null;
}

export interface ResearcherComparison {
  generated_at: string;
  model: string;
  sample_size: number;
  n_common: number;
  n_per_genre: number;
  seed: number;
  wa_mae: { memory: number; grounded: number; delta: number };
  components: ResearcherComponentRow[];
  trust_crowd: string[];
  trust_analogs: string[];
  neutral: string[];
}

export interface TypeSummary {
  books: number;
  avg_wa: number | null;
  avg_total_average: number | null;
  total_words: number;
}

export interface CombinedRankRow {
  rank: number;
  title: string;
  author: string;
  genre: string;
  type: BookKind;
  total_average: number | null;
  wa: number | null;
}

export interface CombinedPerYear {
  year: number;
  fiction: number;
  nonfiction: number;
  books: number;
}

export interface CombinedStatsResponse {
  totals: {
    total_books: number;
    fiction_books: number;
    nonfiction_books: number;
    total_words: number;
    avg_total_average: number | null;
  };
  by_type: { fiction: TypeSummary; nonfiction: TypeSummary };
  tier_distribution: {
    tier_order: string[];
    fiction: Record<string, number>;
    nonfiction: Record<string, number>;
  };
  per_year: CombinedPerYear[];
  combined_ranking: CombinedRankRow[];
}

export interface AddSeriesResult {
  ok: boolean;
  ambiguous: boolean;
  series_canonical?: string;
  total_books?: number;
  already_read?: number;
  already_tbr?: number;
  newly_added?: number;
  appended_to_queue?: number;
  appended_titles?: string[];
  message: string;
  errors?: string[];
}
