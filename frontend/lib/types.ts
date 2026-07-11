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
  // "prediction" = filled from an existing prediction (no LLM call); "llm" = freshly researched.
  source?: "prediction" | "llm";
}

export interface InstantPrediction {
  title: string;
  author: string;
  genre: string;
  wa_final: number;
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
  sourcing?: "memory" | "hybrid";   // which source produced these scores
  hybrid_available?: boolean;        // a grounded (hybrid) upgrade can be fetched
  // Additive conformal 80% interval — present only when the backend has a
  // residual table loaded (calibration/residuals.json). Omitted otherwise.
  wa_low?: number;
  wa_high?: number;
  bucket?: string;                   // internal density-bucket key
  bucket_label?: string;             // human label: author-rich / genre only / …
  pooled?: boolean;                  // half-width borrowed from a neighbour bucket
  calibrated_at?: string;            // residual table generation timestamp
  stale?: boolean;                   // table built by a different engine hash
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
  // Honest 80% prediction interval around the (shrunk) point estimate, keyed by
  // same-author analog density. Optional: absent when no residual table is loaded.
  wa_low?: number;
  wa_high?: number;
  interval_label?: string;
  interval_stale?: boolean;
  // Realistic upside for ranking — the ~76th-percentile outcome (≈ point + 0.45×
  // half-width), a good result beaten ~1 in 4, not the interval ceiling. Surfaces
  // under-rated picks.
  upside?: number;
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

// ── Public track record (walk-forward backtest) — see track_record.py ──
export interface TrackRecordHeadline {
  honest_wa_mae: number; // the non-leaky, "what was knowable then" number
  raw_wa_mae: number; // grounded research → WA, no correction
  naive_wa_mae: number; // predict every book at the mean WA
  n_folds: number; // books actually scored (burn-in excluded)
  n_books_total: number;
  n_burn_in: number;
  burn_in: number; // min training-pool size before a fold is evaluated
}

export interface TrackRecordFold {
  position: number; // chronological read order
  title: string;
  author: string;
  genre: string;
  series: string | null;
  series_number: number | null;
  actual_wa: number;
  predicted_wa: number; // honest variant
  signed_error: number; // predicted − actual
  abs_error: number;
  pool_size: number; // books read before this one
  year_read: number | null;
}

export interface TrackRecordRollingPoint {
  position: number;
  title: string;
  pool_size: number;
  window_n: number; // folds in the trailing window (< window during ramp-up)
  honest_rolling_mae: number;
}

export interface TrackRecordGenreRow {
  genre: string;
  n: number;
  honest_mae: number;
  raw_mae: number;
}

export interface TrackRecordIntervalRow {
  label: string;
  nominal: number; // claimed coverage level (0–1)
  measured: number | null; // observed coverage on the honest folds
  n: number | null;
}

export interface TrackRecord {
  available: boolean;
  provenance: {
    git_head: string;
    engine_hash: string;
    backtest_generated_at: string;
  };
  headline: TrackRecordHeadline;
  folds: TrackRecordFold[];
  rolling: { window: number; series: TrackRecordRollingPoint[] };
  mae_by_genre: TrackRecordGenreRow[];
  interval_coverage: {
    served_conformal: TrackRecordIntervalRow;
    legacy_resid_sd: TrackRecordIntervalRow;
  };
  caveats: string[];
}

/* ── Engine parameters (the public "How the Engine Works" page) ───────────────
   Live engine facts, read from committed data by /api/engine-parameters, so the
   Methodology page interpolates them instead of hardcoding drift-prone numbers.
   Concepts live in the page prose; only these numbers come from the endpoint. */
export interface EngineSchemaCategory {
  category: string;
  components: string[];
}

export interface EngineIntervalBucket {
  key: string;
  label: string;
  half_width?: number; // WA points; present only when the residual table loaded
  n_residuals?: number;
  pooled?: boolean;
}

export interface EngineParameters {
  schema: {
    n_components: number;
    n_categories: number;
    n_genres: number;
    categories: EngineSchemaCategory[];
    component_order: string[];
  };
  // genre → { category → category weight }
  genre_category_weights: Record<string, Record<string, number | null>>;
  // genre → { category → { component → within-category weight } }
  genre_component_weights: Record<string, Record<string, Record<string, number | null>>>;
  shrinkage: {
    corr_blend: number; // correlation-smoothing weight (BLEND)
    k_author: number; // author-deviation shrink strength
    k_genre: number; // genre-estimate shrink strength
    slope_lift: number; // fitted-line → deviation de-compression
    estimator: string; // "n / (n + k)"
  };
  interval: {
    nominal: number; // conformal coverage target (0–1)
    min_bucket_n: number;
    analog_metric: string;
    buckets: EngineIntervalBucket[];
    residuals_available: boolean;
    calibration?: {
      analog_mode: string | null;
      k_author: number | null;
      k_genre: number | null;
      n_residuals: number | null;
    };
  };
  regression: {
    r2: number | null;
    resid_sd: number | null;
    inputs: string[];
  };
  correction: {
    present: boolean;
    applied_in_engine: boolean;
    all_zero?: boolean;
    version?: string | string[];
    decision?: string | string[];
    n_rows?: number;
    n_active?: number;
    max_blend_weight?: number;
  };
  models: {
    research: string;
    discover: string;
  };
  library: {
    n_rated_books: number;
  };
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

// --- Auto re-predict on add -------------------------------------------------
// A finished book re-predicts the unread books whose baseline it moved (same
// author always; same genre past the gate). The pass runs in the background, so
// the add-book response carries a `running` handle the client polls for.
export interface RepredictHandle {
  status: "running";
  token: string;
  trigger: string;
}

export interface RepredictMover {
  title: string;
  reason: "author" | "genre";
  source: string;
  old_wa: number | null;
  new_wa: number;
  d_wa: number | null;
  old_rank: number | null;
  new_rank: number;
  d_rank: number | null;
  drivers: { component: string; delta: number }[];
}

export interface RepredictReport {
  trigger: {
    title: string;
    author?: string;
    genre?: string;
    author_is_new?: boolean;
    n_author_before?: number;
    n_author_after?: number;
    trigger_cached?: boolean;
    researched_now?: boolean;
  };
  genre_gate?: {
    shift: number;
    gate: number;
    fired: boolean;
    wa_pre: number | null;
    wa_post: number | null;
  };
  affected: RepredictMover[];
  suppressed_genre_peers: string[];
  capped_genre_peers: string[];
  cohort_mean_d_wa: number | null;
  note?: string;
}

export type RepredictPoll =
  | { status: "pending" }
  | { status: "done"; report: RepredictReport | null };
