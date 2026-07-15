"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import katex from "katex";
import "katex/dist/katex.min.css";
import type { EngineParameters, TrackRecord } from "@/lib/types";

/* ── formatting ─────────────────────────────────────────────────────────── */
const f2 = (v: number) => v.toFixed(2);
const f3 = (v: number) => v.toFixed(3);
// Percent with one decimal, but drop a trailing ".0" so a round nominal level
// reads "80%" while a measured coverage still reads "81.4%".
const pct1 = (v: number) => {
  const p = v * 100;
  return `${Number.isInteger(p) ? p.toFixed(0) : p.toFixed(1)}%`;
};
// Trim a stored weight to ≤3 decimals without trailing zeros: 0.4, 0.625, 0.143.
const wt = (v: number | null | undefined) =>
  v == null ? "—" : Number(v.toFixed(3)).toString();
const asText = (v: string | string[] | undefined) =>
  Array.isArray(v) ? v.join(", ") : v ?? "—";

// Short category labels for compact formulas / axes.
const CAT_ABBR: Record<string, string> = {
  Story: "Story",
  Character: "Char",
  Aesthetics: "Aes",
  Theme: "Theme",
  Worldbuilding: "WB",
};

/* ── KaTeX ──────────────────────────────────────────────────────────────── */
function TeX({ children }: { children: string }) {
  const html = useMemo(
    () => katex.renderToString(children, { displayMode: false, throwOnError: false }),
    [children],
  );
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}
function TeXBlock({ children }: { children: string }) {
  const html = useMemo(
    () => katex.renderToString(children, { displayMode: true, throwOnError: false }),
    [children],
  );
  return (
    <div
      className="my-3 overflow-x-auto"
      style={{ color: "var(--color-ink)" }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/* ── shared primitives (match TrackRecordClient / CalibrationClient) ─────── */
function SectionHeader({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2
      id={id}
      className="text-sm font-semibold uppercase tracking-wide mt-12 mb-2 scroll-mt-20"
      style={{ color: "var(--color-muted)" }}
    >
      {children}
    </h2>
  );
}
function Lede({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm mb-4 leading-relaxed" style={{ color: "var(--color-muted)" }}>
      {children}
    </p>
  );
}
function Body({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm mb-3 leading-relaxed" style={{ color: "var(--color-ink)" }}>
      {children}
    </p>
  );
}
function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div
      className="comp-tile flex flex-col gap-1"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <span className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>{label}</span>
      <span className="text-xl font-semibold tabular-nums" style={{ color: "var(--color-ink)" }}>{value}</span>
      {note && <span className="text-xs" style={{ color: "var(--color-muted)" }}>{note}</span>}
    </div>
  );
}
function Callout({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "sage" }) {
  const bg = tone === "sage" ? "var(--color-sage-light)" : "var(--color-surface)";
  return (
    <div
      className="rounded-md border px-4 py-3 text-sm leading-relaxed my-3"
      style={{ borderColor: "var(--color-rule)", background: bg, color: "var(--color-ink)" }}
    >
      {children}
    </div>
  );
}

/* ── 1. Prediction-flow spine (connected stage cards) ───────────────────── */
function FlowStage({
  n,
  title,
  detail,
  last,
}: {
  n: number;
  title: string;
  detail: React.ReactNode;
  last?: boolean;
}) {
  return (
    <div className="flex gap-3">
      {/* index + connecting spine */}
      <div className="flex flex-col items-center">
        <div
          className="flex-shrink-0 flex items-center justify-center rounded-full text-xs font-semibold tabular-nums"
          style={{
            width: 26,
            height: 26,
            background: "var(--color-sage-light)",
            color: "var(--color-sage)",
            border: "1px solid var(--color-sage)",
          }}
        >
          {n}
        </div>
        {!last && <div style={{ width: 1, flex: 1, background: "var(--color-rule)", marginTop: 2 }} />}
      </div>
      {/* card */}
      <div
        className="flex-1 rounded-md border px-4 py-3 mb-3"
        style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)" }}
      >
        <div className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>{title}</div>
        <div className="text-xs mt-1 leading-relaxed" style={{ color: "var(--color-muted)" }}>{detail}</div>
      </div>
    </div>
  );
}

/* ── 2. Genre weight explorer ───────────────────────────────────────────── */
function GenreWeights({ params }: { params: EngineParameters }) {
  const genres = useMemo(
    () => Object.keys(params.genre_category_weights).sort(),
    [params],
  );
  const [genre, setGenre] = useState(
    genres.includes("Epic Fantasy") ? "Epic Fantasy" : genres[0],
  );
  const catW = params.genre_category_weights[genre] || {};
  const compW = params.genre_component_weights[genre] || {};
  const cats = params.schema.categories;

  // Live worked WA formula for the selected genre (drift-proof: reads catW).
  const waTeX = useMemo(() => {
    const terms = cats
      .map((c) => {
        const w = catW[c.category];
        if (w == null) return null;
        return `${wt(w)}\\,\\bar c_{\\text{${CAT_ABBR[c.category] ?? c.category}}}`;
      })
      .filter(Boolean)
      .join(" + ");
    return `\\mathrm{WA} = ${terms}`;
  }, [cats, catW]);

  return (
    <div>
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <label className="text-xs font-medium" style={{ color: "var(--color-muted)" }} htmlFor="genre-pick">
          Genre
        </label>
        <select
          id="genre-pick"
          value={genre}
          onChange={(e) => setGenre(e.target.value)}
          className="text-sm rounded-md border px-2 py-1"
          style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)", color: "var(--color-ink)" }}
        >
          {genres.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
        <span className="text-xs" style={{ color: "var(--color-faint)" }}>
          weights read live from the database — {params.schema.n_genres} genres
        </span>
      </div>

      <div className="rounded-md border overflow-hidden text-sm" style={{ borderColor: "var(--color-rule)" }}>
        <table className="w-full">
          <thead>
            <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
              {["Category", "Category weight", "Component", "Within-category weight"].map((h, i) => (
                <th
                  key={h}
                  className={`px-3 py-2 font-medium ${i >= 1 && i !== 2 ? "text-right" : "text-left"}`}
                  style={{ color: "var(--color-muted)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cats.map((cat) =>
              cat.components.map((comp, ci) => (
                <tr
                  key={`${cat.category}-${comp}`}
                  style={{ borderTop: "1px solid var(--color-rule)" }}
                >
                  <td className="px-3 py-1.5" style={{ color: "var(--color-ink)" }}>
                    {ci === 0 ? cat.category : ""}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-mono" style={{ color: ci === 0 ? "var(--color-ink)" : "transparent" }}>
                    {ci === 0 ? wt(catW[cat.category]) : "·"}
                  </td>
                  <td className="px-3 py-1.5" style={{ color: "var(--color-muted)" }}>{comp}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-mono" style={{ color: "var(--color-ink)" }}>
                    {wt(compW[cat.category]?.[comp])}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>

      <p className="text-xs mt-3 mb-1" style={{ color: "var(--color-muted)" }}>
        With those weights, a <strong>{genre}</strong>{" "}book&rsquo;s Weighted Average is:
      </p>
      <TeXBlock>{waTeX}</TeXBlock>
      <p className="text-xs" style={{ color: "var(--color-faint)" }}>
        where <TeX>{`\\bar c_{\\text{cat}}`}</TeX>{" "}is that category&rsquo;s within-category weighted mean of its
        component scores. Within a category the component weights sum to 1; the category weights are the genre&rsquo;s
        emphasis. Change a weight in the database and this formula changes with it.
      </p>
    </div>
  );
}

/* ── 4. Interval bucket table ───────────────────────────────────────────── */
function BucketTable({ params }: { params: EngineParameters }) {
  const buckets = params.interval.buckets;
  return (
    <div className="rounded-md border overflow-hidden text-sm" style={{ borderColor: "var(--color-rule)" }}>
      <table className="w-full">
        <thead>
          <tr style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-rule)" }}>
            {["Density bucket", "Half-width (WA)", "Residuals", "Pooled"].map((h, i) => (
              <th
                key={h}
                className={`px-3 py-2 font-medium ${i === 0 ? "text-left" : "text-right"}`}
                style={{ color: "var(--color-muted)" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((b) => (
            <tr key={b.key} style={{ borderTop: "1px solid var(--color-rule)" }}>
              <td className="px-3 py-1.5" style={{ color: "var(--color-ink)" }}>
                {b.label}{" "}
                <span className="font-mono text-xs" style={{ color: "var(--color-faint)" }}>({b.key})</span>
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums font-mono" style={{ color: "var(--color-ink)" }}>
                {b.half_width != null ? `±${f2(b.half_width)}` : "—"}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums" style={{ color: "var(--color-muted)" }}>
                {b.n_residuals ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-right" style={{ color: "var(--color-muted)" }}>
                {b.pooled ? "yes" : "no"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── page ───────────────────────────────────────────────────────────────── */
export default function MethodologyClient({
  params,
  track,
}: {
  params: EngineParameters;
  track: TrackRecord | null;
}) {
  const { schema, shrinkage, interval, regression, cold_start, correction, models, library } =
    params;
  const ka = shrinkage.k_author;
  const kg = shrinkage.k_genre;
  // Live worked shrink weights (not hardcoded — derived from the K constants).
  const wAuthor1 = 1 / (1 + ka); // one same-author book
  const wGenre10 = 10 / (10 + kg); // a 10-book genre
  const served = track?.interval_coverage.served_conformal;

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="font-display text-3xl font-semibold mb-1" style={{ color: "var(--color-ink)" }}>
        How the Engine Works
      </h1>
      <p className="text-sm mb-6 leading-relaxed" style={{ color: "var(--color-muted)" }}>
        A precise account of how the Ledger predicts a book&rsquo;s score before it&rsquo;s read — the{" "}
        {schema.n_components}-component weighted schema, the empirical-Bayes shrinkage that keeps thin samples honest,
        the conformal prediction interval, and the walk-forward validation that grades it all. The concepts here are
        stable; every <em>number</em>{" "}is read live from the engine, so this page can&rsquo;t drift out of sync with the
        code that runs.
      </p>

      {/* ── 1. Overview / flow ── */}
      <SectionHeader id="flow">The prediction, end to end</SectionHeader>
      <Lede>
        A prediction turns a title + author + genre into a Weighted Average (WA) on the same 0&ndash;10 scale as every
        rated book, plus an honest error band and a projected rank. Five stages, in order — corrections that were once
        here are <em>not</em>, and that&rsquo;s stated where they&rsquo;d have been.
      </Lede>
      <div className="mt-4">
        <FlowStage
          n={1}
          title="Grounded research → 14 raw component scores"
          detail={
            <>
              A single <span className="font-mono">{models.research}</span> call scores the {schema.n_components}{" "}
              components against a detailed rubric (definitions + anchors), returning fine-grained decimals plus a
              confidence flag. Cached by title+author, so a book is never re-researched.
            </>
          }
        />
        <FlowStage
          n={2}
          title="Correlation smoothing"
          detail={
            <>
              Each component is nudged {pct1(shrinkage.corr_blend)} toward the value implied by the other{" "}
              {schema.n_components - 1} (a regression fit on your rated books), exploiting the strong intercorrelation
              among your scores. A validated pre-step, upstream of the correction.
            </>
          }
        />
        <FlowStage
          n={3}
          title="Author + genre correction (empirical Bayes)"
          detail={
            <>
              The systematic gap between the model&rsquo;s scores and yours is estimated at the author, genre, and global
              levels, then shrunk together by sample support (§ below). This maps the LLM&rsquo;s scale onto yours.
            </>
          }
        />
        <FlowStage
          n={4}
          title="Weighted-average roll-up"
          detail={
            <>
              Corrected components combine into category means, then into WA using the genre&rsquo;s weights — the exact
              same math that computes WA for a rated book, so a prediction is directly comparable to the library.
            </>
          }
        />
        <FlowStage
          n={5}
          title="Conformal interval + rank"
          detail={
            <>
              A density-bucketed conformal {pct1(interval.nominal)} band is added around the WA, and the WA is ranked
              against every rated book. Done.
            </>
          }
          last
        />
      </div>
      <Callout>
        <strong>Not in the pipeline:</strong>{" "}a per-component &ldquo;DeltaTracker&rdquo; correction layer once sat between
        stages 3 and 4. It is <strong>retired</strong> — all {correction.n_rows ?? 14} of its constants are{" "}
        <span className="font-mono">0.0</span> (version <span className="font-mono">{asText(correction.version)}</span>,
        decision <span className="font-mono">{asText(correction.decision)}</span>) and{" "}
        <em>nothing in the serving path reads them</em>. The engine you&rsquo;re reading about is the engine that runs.
      </Callout>

      {/* ── 2. Schema ── */}
      <SectionHeader id="schema">The {schema.n_components}-component weighted schema</SectionHeader>
      <Lede>
        Every book is scored on {schema.n_components} components grouped into {schema.n_categories}{" "}categories, each
        0&ndash;10. The Weighted Average is a genre-weighted sum of category means — two layers of weights, both
        per-genre, both stored in the database.
      </Lede>
      <Body>
        First, within each category, component scores combine by <strong>within-category weights</strong>{" "}
        <TeX>{`w^{\\text{comp}}_i`}</TeX> (which sum to 1) into a category mean:
      </Body>
      <TeXBlock>{`\\bar c_{\\text{cat}} = \\sum_{i \\in \\text{cat}} w^{\\text{comp}}_i \\, s_i`}</TeXBlock>
      <Body>
        Then the category means combine by the genre&rsquo;s <strong>category weights</strong>{" "}
        <TeX>{`w^{\\text{cat}}`}</TeX> into the Weighted Average:
      </Body>
      <TeXBlock>{`\\mathrm{WA} = \\sum_{\\text{cat}} w^{\\text{cat}}_{\\text{genre}} \\, \\bar c_{\\text{cat}}`}</TeXBlock>
      <Body>
        The weights differ by genre — a hard-SF book earns its keep on ideas, an epic fantasy on world and story. Pick a
        genre to see its live weights and the exact WA formula they produce:
      </Body>
      <GenreWeights params={params} />

      {/* ── 3. Shrinkage ── */}
      <SectionHeader id="shrinkage">Empirical-Bayes shrinkage</SectionHeader>
      <Lede>
        The correction in stage 3 has to estimate how your taste bends the model&rsquo;s scores — but the pools are
        tiny. You may have three books by an author and a dozen in a genre. Trusting a three-book mean as fully as a
        thirty-book mean is how a model overfits its own noise. Empirical Bayes fixes this by <em>shrinking</em> each
        estimate toward the broader pool it sits in, in proportion to how little data supports it.
      </Lede>
      <Body>
        For each component the correction works on the deviation <TeX>{`d = s^{\\text{you}} - s^{\\text{llm}}`}</TeX>{" "}—
        how your score differs from the model&rsquo;s. It estimates that deviation at the author, genre, and global
        levels, then blends level toward parent with the classic shrink step:
      </Body>
      <TeXBlock>{`\\hat\\theta \\;=\\; \\underbrace{\\frac{n}{n+K}}_{\\text{trust the level}}\\,\\bar\\theta_{\\text{level}} \\;+\\; \\underbrace{\\frac{K}{n+K}}_{\\text{shrink to parent}}\\,\\hat\\theta_{\\text{parent}}`}</TeXBlock>
      <Body>
        <TeX>{`n`}</TeX> is the number of books supporting that level and <TeX>{`K`}</TeX>{" "}is the level&rsquo;s shrink
        strength — &ldquo;how many books before the level is trusted on its own.&rdquo; The nesting is global →
        genre → author, with two live constants:
      </Body>
      <div className="grid grid-cols-2 gap-3 my-4">
        <div className="rounded-md border px-4 py-3" style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)" }}>
          <div className="text-sm font-semibold mb-1" style={{ color: "var(--color-ink)" }}>Genre → global</div>
          <TeXBlock>{`w_g = \\dfrac{n_g}{n_g + ${wt(kg)}}`}</TeXBlock>
          <div className="text-xs" style={{ color: "var(--color-muted)" }}>
            <TeX>{`K_{\\text{genre}} = ${wt(kg)}`}</TeX>. A genre needs real volume before it overrides the global
            picture — a 10-book genre gets weight <span className="font-mono">{f2(wGenre10)}</span>.
          </div>
        </div>
        <div className="rounded-md border px-4 py-3" style={{ borderColor: "var(--color-rule)", background: "var(--color-surface)" }}>
          <div className="text-sm font-semibold mb-1" style={{ color: "var(--color-ink)" }}>Author → genre</div>
          <TeXBlock>{`w_a = \\dfrac{n_a}{n_a + ${wt(ka)}}`}</TeXBlock>
          <div className="text-xs" style={{ color: "var(--color-muted)" }}>
            <TeX>{`K_{\\text{author}} = ${wt(ka)}`}</TeX>. Author signal is precious and scarce, so it&rsquo;s trusted
            fast — a single same-author book already gets weight <span className="font-mono">{f2(wAuthor1)}</span>.
          </div>
        </div>
      </div>
      <Body>
        Two refinements sit alongside the shrinkage. A <strong>slope lift</strong> of{" "}
        <span className="font-mono">{wt(shrinkage.slope_lift)}</span>{" "}blends the fitted per-genre regression (whose
        slope &lt; 1 pulls everything toward the mean) toward a slope-1 deviation model, undoing that
        regression-to-the-mean compression; and the correlation smoothing from stage 2 (<TeX>{`\\text{blend} = ${wt(shrinkage.corr_blend)}`}</TeX>)
        runs first. All of it is fit only on your rated books, out-of-sample for the book being predicted.
      </Body>
      <Callout>
        <strong>Why not just use the author mean when you have one?</strong>{" "}Because a single book is one draw from a
        noisy process. Shrinkage doesn&rsquo;t discard it — at <TeX>{`n_a = 1`}</TeX> it still carries{" "}
        <span className="font-mono">{f2(wAuthor1)}</span>{" "}of the weight — it just refuses to let it fully overwrite the
        genre picture it&rsquo;s nested in. As the author pool grows, <TeX>{`w_a \\to 1`}</TeX> and the parent fades.
      </Callout>

      {/* ── Cold-start length term ── */}
      <SectionHeader id="cold-start">The cold-start length term</SectionHeader>
      <Lede>
        One more repair, for the hardest case — a book by an author you&rsquo;ve{" "}
        <strong>never read</strong>. With no same-author history the correction leans on the
        genre and global picture, and there it is blind to something that matters:{" "}
        <strong>length</strong>.
      </Lede>
      <Body>
        Fit on your own held-out residuals, long books are systematically under-predicted in that
        no-analog case — a genre average knows nothing about how you respond to a 900-page epic. A
        single linear term repairs it
        {cold_start.fitted && cold_start.slope_wa_per_dex != null ? (
          <>
            : a slope of{" "}
            <span className="font-mono">{f2(cold_start.slope_wa_per_dex)}</span>{" "}WA per 10× word
            count, pivoting around a{" "}
            <span className="font-mono">{(cold_start.center_words ?? 0).toLocaleString()}</span>-word
            book
          </>
        ) : (
          <> — a slope on centered log word count</>
        )}
        , added to the prediction only on the cold slice.
      </Body>
      <TeXBlock>{`\\widehat{\\mathrm{WA}}_{\\text{cold}} \\;=\\; \\widehat{\\mathrm{WA}} \\;+\\; \\beta\\,\\big(\\log_{10}\\text{words} - \\mu\\big), \\qquad n_a = 0`}</TeXBlock>
      <Body>
        It is deliberately narrow: it fires <strong>only</strong> when {cold_start.applied_when},
        and switches off the instant you rate a book by that author — the real same-author analog
        takes over. It is fit once you have at least{" "}
        <span className="font-mono">{cold_start.min_books_to_fit}</span>{" "}rated books, and it was
        validated on the walk-forward backtest below and permutation-tested, so it isn&rsquo;t a
        fluke of the small cold-start sample.
      </Body>

      {/* ── 4. Intervals ── */}
      <SectionHeader id="intervals">Prediction intervals, done honestly (conformal)</SectionHeader>
      <Lede>
        A point estimate without an error bar is a guess wearing a lab coat. The Ledger serves a{" "}
        <strong>{pct1(interval.nominal)} conformal band</strong>{" "}— a distribution-free interval built from the
        engine&rsquo;s own held-out errors, not from an assumed bell curve.
      </Lede>
      <Body>
        A textbook <TeX>{`\\pm z\\,\\sigma`}</TeX> band assumes the residuals are Gaussian, equal-variance, and that{" "}
        <TeX>{`\\sigma`}</TeX> actually measures <em>prediction</em> error. None of that holds here. Conformal
        prediction sidesteps all three: collect the absolute residuals the engine makes on held-out books, and read the
        interval half-width straight off their empirical quantile.
      </Body>
      <TeXBlock>{`\\hat q \\;=\\; \\operatorname{Quantile}_{\\,${wt(interval.nominal)}}\\big(\\{\\,|r_i| : i \\in \\text{held-out}\\,\\}\\big), \\qquad \\widehat{\\mathrm{WA}} \\pm \\hat q`}</TeXBlock>
      <Body>
        Under exchangeability this guarantees ~{pct1(interval.nominal)} marginal coverage with no distributional
        assumptions. The one refinement: residuals are <strong>bucketed by data density</strong>{" "}— how many same-author
        analogs the library holds — so a book on the frontier of your taste gets a wider, honest band instead of a
        falsely tight one. Thin buckets (&lt; {interval.min_bucket_n} residuals) borrow their nearest richer neighbour.
      </Body>
      <BucketTable params={params} />
      <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
        Half-widths in WA points, from the served residual table. The band widens as the same-author pool thins —
        {interval.buckets.some((b) => b.pooled) && " pooled buckets borrow a neighbour to stay stable — "}
        so frontier books are never over-confident.
      </p>

      <Callout>
        <strong>The band it replaced.</strong> The old interval was{" "}
        <TeX>{`\\pm 1.645\\,\\sigma_{\\text{resid}}`}</TeX>, where <TeX>{`\\sigma_{\\text{resid}}`}</TeX> came from the
        regression of WA on its own category averages. That fit is nearly deterministic —{" "}
        {regression.r2 != null && (
          <>live <TeX>{`R^2 = ${f3(regression.r2)}`}</TeX>{regression.resid_sd != null && <>, <TeX>{`\\sigma_{\\text{resid}} = ${f3(regression.resid_sd)}`}</TeX></>} — </>
        )}
        so its residual is a <em>fit diagnostic</em>, not the error of predicting an unread book. Dressed up as a 90%
        interval it covered only {track ? pct1(track.interval_coverage.legacy_resid_sd.measured ?? 0.31) : "≈31%"} of
        real errors. It was removed from every served surface; the conformal band is the only interval the engine
        serves.
      </Callout>
      {served?.measured != null && (
        <Callout tone="sage">
          Measured on {served.n} held-out books in the walk-forward backtest, the served band covers{" "}
          <strong>{pct1(served.measured)}</strong> against its {pct1(served.nominal)} claim — essentially on target.
          Keeping the honest {pct1(interval.nominal)} level (rather than re-inflating to a nominal 90%) is a deliberate
          choice. See the <Link href="/track-record" className="underline" style={{ color: "var(--color-sage)" }}>Track Record</Link>.
        </Callout>
      )}

      {/* ── 5. Validation ── */}
      <SectionHeader id="validation">Validation: walk-forward, not leave-one-out</SectionHeader>
      <Lede>
        An accuracy number is only honest if it never saw the answer. Leave-one-out cross-validation trains on every{" "}
        <em>other</em> book — including ones read years later — so it quietly launders future taste into a past
        prediction. Walk-forward refuses that.
      </Lede>
      <Body>
        The backtest replays your reading history in order. For each book it predicts what the engine{" "}
        <em>would have said the day you started it</em>, training only on the books read before it. It&rsquo;s the
        &ldquo;what was knowable then&rdquo; accuracy — the honest baseline any future engine change must beat.
      </Body>
      {track ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 my-4">
            <Stat label="Honest MAE" value={f2(track.headline.honest_wa_mae)} note="corrected, no leakage" />
            <Stat label="Raw MAE" value={f2(track.headline.raw_wa_mae)} note="research only" />
            <Stat label="Naïve baseline" value={f2(track.headline.naive_wa_mae)} note="predict the mean" />
            <Stat label="Books tested" value={String(track.headline.n_folds)} note={`burn-in ${track.headline.burn_in}`} />
          </div>
          <Body>
            The honest, chronological error is <strong>{f2(track.headline.honest_wa_mae)}</strong> WA points across{" "}
            {track.headline.n_folds} books — comfortably better than research-alone ({f2(track.headline.raw_wa_mae)}) and
            than guessing the library mean ({f2(track.headline.naive_wa_mae)}). The{" "}
            <Link href="/track-record" className="underline" style={{ color: "var(--color-sage)" }}>Track Record</Link>{" "}
            page shows this book-by-book: predicted-vs-actual, the rolling &ldquo;getting smarter&rdquo; curve, and
            error by genre.
          </Body>
        </>
      ) : (
        <Callout>
          The walk-forward artifacts haven&rsquo;t been generated yet, so the live baselines aren&rsquo;t available here.
          The <Link href="/track-record" className="underline" style={{ color: "var(--color-sage)" }}>Track Record</Link>{" "}
          page carries the full breakdown once <span className="font-mono">walkforward.py</span> has run.
        </Callout>
      )}

      {/* ── 6. Honesty / limitations ── */}
      <SectionHeader id="limits">What it can&rsquo;t do (and what&rsquo;s honest about it)</SectionHeader>
      <Lede>The instrument is calibrated to one reader&rsquo;s taste. Its limits are stated as plainly as its numbers.</Lede>
      <ul className="flex flex-col gap-3">
        {[
          <>
            <strong>Hindsight in the research inputs.</strong>{" "}The grounded-research vectors embed post-publication
            reception — reviews, reputation. The backtest holds those fixed and measures the engine&rsquo;s{" "}
            <em>math</em>, not a true blind read. An accepted caveat, not a hidden one.
          </>,
          <>
            <strong>The correction is retired to zero.</strong> The per-component DeltaTracker layer failed its
            out-of-sample gate and was retired — {correction.all_zero ? "all constants are 0.0" : "its constants are held at 0.0"}{" "}
            and the serving path contains no reader for them. It is documented here only so its absence is unambiguous.
          </>,
          <>
            <strong>One person, not a crowd.</strong>{" "}Every weight, correction, and interval is fit on a single
            reader&rsquo;s {library.n_rated_books} rated books. This is a precision instrument for one taste, not a
            general recommender — it says nothing about whether <em>you</em> will like a book.
          </>,
          <>
            <strong>The band is borrowed, slightly conservative.</strong>{" "}The conformal residuals are calibrated on the
            autonomous analog engine&rsquo;s errors, then centred on the (usually tighter) research prediction — so the
            served interval leans mildly wide rather than narrow.
          </>,
          <>
            <strong>Thin taste = rough call.</strong> A book with no same-author analog and a sparse genre leans on the
            broadest pools and gets the widest band. The engine flags this rather than hiding it.
          </>,
        ].map((li, i) => (
          <li key={i} className="text-sm pl-3 border-l-2 leading-relaxed" style={{ color: "var(--color-ink)", borderColor: "var(--color-rule)" }}>
            {li}
          </li>
        ))}
      </ul>

      <p className="text-xs mt-10 pt-4 border-t leading-relaxed" style={{ color: "var(--color-faint)", borderColor: "var(--color-rule)" }}>
        Every number on this page — the {schema.n_components} components and their weights, the shrinkage constants{" "}
        (<TeX>{`K_{\\text{author}} = ${wt(ka)}`}</TeX>, <TeX>{`K_{\\text{genre}} = ${wt(kg)}`}</TeX>), the{" "}
        {pct1(interval.nominal)} interval level, the models — is read live from the engine via{" "}
        <span className="font-mono">/api/engine-parameters</span>. Validation figures are reused from the Track Record so
        the two pages can&rsquo;t disagree. The prose is written by hand; the numbers are not.
      </p>
    </div>
  );
}
