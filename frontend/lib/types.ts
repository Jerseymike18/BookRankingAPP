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
  /** Nonfiction's primary ranking score (the shared card leads with this for the
   *  nonfiction track). Absent for fiction, which ranks by WA. */
  total_average?: number;
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

/** Public /try demo prediction (unauthenticated). A success is a full
 *  ResearchResult plus `available:true`; a book that isn't already analyzed AND
 *  can't be predicted live right now (the live budget is spent) comes back as
 *  `{ available:false, message }` so the client offers the always-free examples. */
export type DemoPrediction =
  | (ResearchResult & { available: true })
  | {
      available: false;
      message: string;
      title: string;
      author: string;
      genre: string | null;
    };

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
  // Fiction-shaped parity fields (so the shared Predict card consumes this
  // directly). Nonfiction has no web-grounding path and no residual table, so
  // sourcing is always "memory", hybrid_available is false, and no interval is
  // ever attached.
  n_genre?: number;
  n_author?: number;
  from_cache?: boolean;
  words?: number | null;
  series?: string;
  series_number?: number | null;
  blurb?: string;
  keywords?: string;
  sourcing?: "memory" | "hybrid";
  hybrid_available?: boolean;
  genre_auto_detected?: boolean;
}

export interface NonfictionCandidate {
  title: string;
  author: string;
  /** Always "Nonfiction" (the shared candidate table shows it as a genre chip). */
  genre?: string | null;
  /** True once this book has been researched (free to score) — cached/new column. */
  cached?: boolean;
  series?: string | null;
  series_number?: number | null;
  /** True for the exact book the reader named — pinned to the top of the table. */
  requested?: boolean;
}

export interface NonfictionDiscoverResponse {
  candidates: NonfictionCandidate[];
  request: string;
  note?: string;
  /** Always empty for nonfiction (no Goodreads series provenance); present for
   *  shape-parity with the fiction DiscoverCandidatesResponse. */
  sources?: string[];
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
  /** True for the exact book the reader named — injected + pinned to the top. */
  requested?: boolean;
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
  // DIRECTIONAL 80% band + upside around the predicted WA (mirrors the fiction
  // Recommendation fields). Small-sample conformal — labeled directional — and
  // omitted when the nonfiction library is too small to fit one.
  wa_low?: number;
  wa_high?: number;
  upside?: number;
  interval_label?: string;
}

// Nonfiction read-queue payload — the not-done nonfiction TBR. Mirrors the fiction
// ReadQueueResponse shape (recommendations + genres), but over nonfiction recs.
// `genres` is currently always empty from the endpoint (filter derives from recs).
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

// One (year, month) bucket of the by-month breakdown. Only books with a
// backfilled read_month appear. Carries the full set of monthly reading stats.
export interface TimelineMonthRow {
  year: number;
  month: number; // 1-12
  books: number;
  total_words: number | null;
  avg_words: number | null;
  avg_wa: number | null;
  avg_total_average: number | null;
  genres: number | null;   // distinct genres read that month
  authors: number | null;  // distinct authors read that month
  top_book: string | null;  // highest-WA book that month
  top_wa: number | null;
  // Category averages keyed by lowercased category name (story/character/… or
  // quality/aesthetics/theme). String index covers top_book too.
  [key: string]: number | string | null;
}

export interface TimelineResponse {
  rows: TimelineRow[];
  months: TimelineMonthRow[];
  categories: string[];
}

export interface DeltaLogEntry {
  id: number;
  title: string;
  logged_at: string;
  read_year: number | null;
  read_month: number | null; // 1-12
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

// Walk-forward validation result (POST /api/calibration/walkforward): each book
// predicted by an engine fit only on the books read before it. Same shape as
// the old LOO result plus the walk-forward-specific counters; naive_mae is the
// past-only mean baseline.
export interface WalkforwardResult {
  n_books: number;
  n_evaluated: number;
  burn_in: number;
  n_unordered: number;
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

// ── Personal Track Record (per-user delta_log grading) — see track_record.py ──
export interface TrackRecordHeadline {
  wa_mae: number;              // mean |pred_wa − act_wa| over the reader's finished predictions
  raw_wa_mae: number | null;   // uncorrected research vector; null when too few rows carry corr_wa
  naive_wa_mae: number;        // mean |act_wa − mean(act_wa)| — guessing the reader's average
  n_books: number;             // finished + genuinely-predicted books this reader has
}

export interface TrackRecordFold {
  position: number;            // reading order, oldest → newest
  title: string;
  author: string;
  genre: string;
  series: string | null;
  series_number: number | null;
  actual_wa: number;
  predicted_wa: number;        // frozen at forecast time
  signed_error: number;        // predicted − actual
  abs_error: number;
  pool_size: number;           // books read before this one (== position)
  year_read: number | null;
}

export interface TrackRecordRollingPoint {
  position: number;
  title: string;
  pool_size: number;
  window_n: number;            // rows in the trailing window (< window during ramp-up)
  honest_rolling_mae: number;
}

export interface TrackRecordGenreRow {
  genre: string;
  n: number;
  honest_mae: number;
  raw_mae: number | null;      // null when this genre has no rows carrying corr_wa
}

export interface TrackRecordIntervalRow {
  label: string;
  nominal: number;             // claimed coverage level (0–1)
  measured: number | null;     // observed coverage on this reader's rows
  n: number | null;
}

export interface TrackRecord {
  available: boolean;
  provenance: {
    data_source: "personal";
    min_books: number;
  };
  headline: TrackRecordHeadline;
  folds: TrackRecordFold[];
  rolling: { window: number; series: TrackRecordRollingPoint[] };
  mae_by_genre: TrackRecordGenreRow[];
  interval_coverage: {
    served_conformal: TrackRecordIntervalRow;
    // legacy_resid_sd removed: the retired resid_sd band is not served, so
    // its coverage is not shown. See MEMORY.md → project_track_record_page.md.
  };
  caveats: string[];
}

// ── Engine-wide walk-forward validation (reference library) — engine_validation.py ──
export interface EngineValidation {
  available: boolean;
  provenance: {
    git_head: string;
    engine_hash: string;
    backtest_generated_at: string;
  };
  headline: {
    honest_wa_mae: number;
    raw_wa_mae: number;
    naive_wa_mae: number;
    n_folds: number;
    n_books_total: number;
    burn_in: number;
  };
  served_coverage: {
    nominal: number;             // 0.80 — the served conformal band's claim
    measured: number | null;     // observed on the walk-forward folds
    n: number | null;
  };
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
  cold_start: {
    applied_when: string;
    feature: string;
    fit: string;
    min_books_to_fit: number;
    // Where THIS reader's length slope comes from: fitted on their own
    // residuals, their onboarding word-count preference, or off entirely.
    source: "fitted" | "preference" | "off";
    fitted: boolean;
    author_prior: boolean; // favorite-authors bump attached (new readers)
    slope_wa_per_dex?: number; // length slope (WA per 10× word count)
    center_words?: number; // pivot word count (10^μ)
    n_books_fit?: number; // present only when source === "fitted"
  };
  models: {
    research: string;
    discover: string;
  };
  library: {
    n_rated_books: number;
    // "own" once the reader's library fits its own calibration;
    // "borrowed_seed" while a new tenant rides the reference library's.
    model_source: "own" | "borrowed_seed";
    min_own_fit: number | null;
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

/* ── Genre / component weights editor (per-tenant tailoring) ──────────────── */

/** One editable weight group: its effective values (global default overlaid with
 *  the user's override), the pristine default (for "reset"), and whether the user
 *  has customized it. Values are normalized to sum 1.0. */
export interface WeightGroup {
  effective: Record<string, number>;
  default: Record<string, number>;
  customized: boolean;
}

/** A genre's within-category component split (e.g. Story → Plot/…/Ending). */
export interface CategoryWeightGroup extends WeightGroup {
  category: string;
  components: string[];
}

export interface GenreWeights {
  genre: string;
  /** True for a private genre the user created (vs a shared global genre). */
  custom: boolean;
  /** The category weights (Story/Character/Theme/Aesthetics/Worldbuilding, or the
   *  3 nonfiction categories). */
  category_weights: WeightGroup;
  /** Per-category component splits — only categories that have components. */
  categories: CategoryWeightGroup[];
}

export interface EffectiveWeights {
  categories: string[]; // canonical category order
  genres: GenreWeights[];
}

/* ── Public profiles (opt-in cross-user browse) ── */

/** The caller's OWN profile record (settings page). Null when unclaimed. */
export interface Profile {
  user_id: string;
  handle: string;
  display_name: string | null;
  is_public: boolean;
  created_at: string | null;
  updated_at: string | null;
}

/** A public profile's header + library sizes (directory card + profile page). */
export interface PublicProfile {
  handle: string;
  display_name: string | null;
  fiction_books: number;
  nonfiction_books: number;
}

export interface ProfileDirectory {
  profiles: PublicProfile[];
}

/* ── Goodreads import (onboarding) ────────────────────────────────────────── */

/** One staged import row — enriched METADATA only, never scores. Mirrors
 * db_write._staging_row. `shelf` routes it (read → ranking backlog; to-read /
 * currently-reading → recommendation on commit); `enrich_state` tracks the
 * background classify. `goodreads_rating` (1–5) is a memory hint, never a score. */
export interface ImportStagingRow {
  id: string;
  shelf: "read" | "to-read" | "currently-reading";
  kind: "fiction" | "nonfiction" | null;
  title: string;
  author: string | null;
  genre: string | null;
  series: string | null;
  series_number: number | null;
  words: number | null;
  year_read: number | null;
  read_month: number | null;
  goodreads_rating: number | null;
  goodreads_review: string | null;
  state: string;
  enrich_state: "pending" | "done" | "error" | string;
}

export interface ImportParseSummary {
  total_data_rows: number;
  kept: number;
  dropped_no_title: number;
  dropped_bad_shelf: number;
  dropped_dupe_in_csv: number;
  by_shelf: Record<string, number>;
}

export interface ImportUploadResult {
  ok: boolean;
  parse: ImportParseSummary;
  enriching: boolean;
  batch_id: string;
  staged: number;
  skipped_existing: number;
  skipped_bad: number;
}

export interface ImportStagingResponse {
  rows: ImportStagingRow[];
  count: number;
  by_shelf: Record<string, number>;
  by_enrich: Record<string, number>;
}

export interface ImportStatus {
  total: number;
  by_state: Record<string, number>;
  by_enrich: Record<string, number>;
}

export interface ImportCommitResult {
  ok: boolean;
  committed: number;
  skipped: { id: string; title: string; reason: string }[];
  backlog: number;
}
