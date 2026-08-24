"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import type { GenreEvidence, GenrePick, GenreSurprise } from "@/lib/types";
import { recommendGenres, isCancelled } from "@/lib/api";
import { usePredictJobs, isRunBusy } from "@/lib/predict-jobs";
import type { GenreTabState } from "@/lib/predict-jobs";
import { ProgressBar } from "@/components/ProgressBar";
import { Card, SageButton, ErrorBox, InfoBox, inputStyle } from "../ui";

/* ═══════════════════════════════════════════════════════════════════════════
   GENRE PREDICTION — "what should I read more of"
   ═══════════════════════════════════════════════════════════════════════════
   The other half of Predict. Book prediction (/predict) answers "how much will
   I like THIS book"; this answers the question that comes before it — which
   KINDS of book are worth pointing that machinery at. Until this existed,
   Discover's generator saw the reader's titles and their genre list and not one
   number about how they actually rate genres, so the genre was whatever they
   typed.

   Every pick hands its `discover_request` to the book-prediction page, so the
   route from "read more Gothic" to actual scored books runs through the
   pipeline that already exists — no second scoring path.

   The numbers rendered here come from `genre_affinity.genre_evidence` via the
   API payload, NEVER from the model's prose (see genre_affinity.py). The "types"
   half deliberately shows no numbers at all — it has no data behind it, and the
   backend drops any entry that claims otherwise. */

function pct(n: number) {
  return `${Math.round(n * 100)}%`;
}

/** One evidence figure, rendered the same way everywhere it appears. */
function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="text-xs whitespace-nowrap" style={{ color: "var(--color-muted)" }}>
      <span style={{ color: "var(--color-faint)" }}>{label} </span>
      {children}
    </span>
  );
}

function SurpriseNote({ surprise }: { surprise: GenreSurprise }) {
  // Positive = the engine under-predicts this genre for them, i.e. they enjoy it
  // MORE than the model expects. Sage for that, muted for the reverse — never a
  // red/green pair, which would read as good/bad rather than as a direction.
  const under = surprise.mean_signed > 0;
  return (
    <span
      className="text-xs whitespace-nowrap"
      style={{ color: under ? "var(--color-sage)" : "var(--color-muted)" }}
      title={
        under
          ? "Your ratings come in ABOVE what the engine predicted for this genre — you enjoy it more than the model expects."
          : "Your ratings come in BELOW what the engine predicted for this genre — the model is optimistic here."
      }
    >
      {under ? "engine under-rates" : "engine over-rates"} by{" "}
      {Math.abs(surprise.mean_signed).toFixed(2)} over {surprise.n}
    </span>
  );
}

function GenrePickCard({
  pick,
  handoffDisabled,
  onUse,
}: {
  pick: GenrePick;
  handoffDisabled: boolean;
  onUse: () => void;
}) {
  return (
    <div
      className="rounded-lg p-4"
      style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}
    >
      <div className="flex items-start gap-3 flex-wrap">
        <span className="genre-chip">{pick.genre}</span>
        {pick.affinity !== null ? (
          <span className="wa-badge">{pick.affinity.toFixed(2)}</span>
        ) : (
          <span className="text-xs" style={{ color: "var(--color-faint)" }}>
            no rated books yet
          </span>
        )}
        <span
          className="text-xs px-2 py-0.5 rounded-md"
          style={{ background: "var(--color-surface)", color: "var(--color-muted)" }}
        >
          {pick.confidence} confidence
        </span>
      </div>

      {/* The evidence, straight from the payload. Deliberately above the prose:
          the numbers are the argument, the sentence is the gloss. */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        {pick.affinity !== null && pick.band_low !== null && pick.band_high !== null && (
          <Figure label="80% band">
            {pick.band_low.toFixed(2)}–{pick.band_high.toFixed(2)}
          </Figure>
        )}
        <Figure label="books">
          {pick.n_books} ({pick.evidence_tier})
        </Figure>
        {pick.surprise && <SurpriseNote surprise={pick.surprise} />}
        <Figure label="on your TBR">{pick.tbr_open}</Figure>
      </div>

      {pick.case && (
        <p className="text-sm mt-3" style={{ color: "var(--color-ink)" }}>
          {pick.case}
        </p>
      )}

      <div className="flex items-center gap-3 mt-3 flex-wrap">
        <SageButton onClick={onUse} disabled={handoffDisabled} variant="secondary">
          Find books like this
        </SageButton>
        <span className="text-xs italic flex-1 min-w-0" style={{ color: "var(--color-faint)" }}>
          “{pick.discover_request}”
        </span>
      </div>
    </div>
  );
}

function EvidenceTable({ rows }: { rows: GenreEvidence[] }) {
  return (
    <div className="overflow-x-auto mt-3">
      <table className="w-full text-sm" style={{ color: "var(--color-ink)" }}>
        <thead>
          <tr style={{ color: "var(--color-muted)" }} className="text-xs uppercase tracking-widest">
            <th className="text-left font-semibold py-1.5 pr-3">Genre</th>
            <th className="text-right font-semibold py-1.5 px-2">Books</th>
            <th className="text-right font-semibold py-1.5 px-2">Share</th>
            <th className="text-right font-semibold py-1.5 px-2">Affinity</th>
            <th className="text-right font-semibold py-1.5 px-2">80% band</th>
            <th className="text-right font-semibold py-1.5 px-2">Surprise</th>
            <th className="text-right font-semibold py-1.5 pl-2">TBR</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.genre} style={{ borderTop: "1px solid var(--color-rule)" }}>
              <td className="py-1.5 pr-3">
                {r.genre}
                {r.status === "unread" && (
                  <span className="ml-2 text-xs" style={{ color: "var(--color-faint)" }}>
                    unread
                  </span>
                )}
              </td>
              <td className="py-1.5 px-2 text-right tabular-nums">{r.n_books || "—"}</td>
              {/* Read share sits NEXT TO affinity on purpose: how much of the
                  library a genre occupies is a different claim from how highly
                  it scores, and the two are easy to conflate. */}
              <td
                className="py-1.5 px-2 text-right tabular-nums"
                style={{ color: "var(--color-muted)" }}
              >
                {r.n_books ? pct(r.read_share) : "—"}
              </td>
              <td className="py-1.5 px-2 text-right tabular-nums">
                {r.affinity !== null ? r.affinity.toFixed(2) : "—"}
              </td>
              <td
                className="py-1.5 px-2 text-right tabular-nums"
                style={{ color: "var(--color-muted)" }}
              >
                {r.band_low !== null && r.band_high !== null
                  ? `${r.band_low.toFixed(2)}–${r.band_high.toFixed(2)}`
                  : "—"}
              </td>
              <td
                className="py-1.5 px-2 text-right tabular-nums"
                style={{
                  color:
                    r.surprise && r.surprise.mean_signed > 0
                      ? "var(--color-sage)"
                      : "var(--color-muted)",
                }}
              >
                {r.surprise
                  ? `${r.surprise.mean_signed > 0 ? "+" : ""}${r.surprise.mean_signed.toFixed(2)}`
                  : "—"}
              </td>
              <td className="py-1.5 pl-2 text-right tabular-nums">{r.tbr_open || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GenreRecommender({
  handoffDisabled,
  state,
  patch,
  onUseRequest,
}: {
  /** A fiction scoring run is in flight, so handing it a NEW request would
   *  overwrite the one it is using. Blocks only the hand-off buttons — asking
   *  for a recommendation is read-only and never conflicts with a run. */
  handoffDisabled: boolean;
  state: GenreTabState;
  patch: (p: Partial<GenreTabState>) => void;
  /** Hands a ready-made request to the Books tab and takes the reader there. */
  onUseRequest: (request: string) => void;
}) {
  const { focus, loading, error, result, showEvidence } = state;
  const setFocus = (v: string) => patch({ focus: v });
  const setError = (v: string | null) => patch({ error: v });
  const setShowEvidence = (fn: (v: boolean) => boolean) =>
    patch({ showEvidence: fn(showEvidence) });

  async function run() {
    patch({ loading: true, error: null });
    try {
      patch({ result: await recommendGenres(focus), loading: false });
    } catch (e) {
      if (isCancelled(e)) patch({ loading: false });
      else patch({ error: e instanceof Error ? e.message : String(e), loading: false });
    }
  }

  return (
    <Card>
      <h2 className="font-display font-semibold text-base mb-1" style={{ color: "var(--color-ink)" }}>
        What should I read more of?
      </h2>
      <p className="text-xs mb-4" style={{ color: "var(--color-muted)" }}>
        Reads your <strong>fiction</strong> ratings — how each genre actually scores, how
        thin the evidence is, and where the engine has been wrong about you — and argues
        for a few. One cheap call; nothing is saved.
      </p>

      <input
        className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
        style={inputStyle}
        value={focus}
        onChange={(e) => setFocus(e.target.value)}
        placeholder="Optional steer — e.g. I want to branch out · something shorter"
        disabled={loading}
      />

      <div className="flex items-center gap-4 mt-3">
        <SageButton onClick={run} disabled={loading}>
          {loading ? "Reading your ratings…" : "Recommend genres"}
        </SageButton>
      </div>

      {loading && (
        <ProgressBar
          className="mt-3"
          label="Weighing your genres…"
          hint="One call — the evidence itself is computed locally from your library."
        />
      )}

      {error && (
        <div className="mt-3">
          <ErrorBox message={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {result && (
        <div className="mt-4 flex flex-col gap-3">
          {result.genres.length === 0 && !error && (
            <InfoBox message="No genre stood out clearly enough to recommend — your ratings are evenly spread." />
          )}

          {result.genres.map((g) => (
            <GenrePickCard
              key={g.genre}
              pick={g}
              handoffDisabled={handoffDisabled}
              onUse={() => onUseRequest(g.discover_request)}
            />
          ))}

          {result.types.length > 0 && (
            <>
              <p className="text-xs uppercase tracking-widest mt-2" style={{ color: "var(--color-muted)" }}>
                Kinds of book your genre labels don&apos;t capture
              </p>
              {/* No numbers here, by construction: these are hypotheses drawn from
                  the component evidence, not measurements of a rated group. */}
              {result.types.map((t) => (
                <div
                  key={t.label}
                  className="rounded-lg p-4"
                  style={{ background: "var(--color-surface-2)", border: "1px dashed var(--color-rule)" }}
                >
                  <p className="font-display font-semibold text-sm" style={{ color: "var(--color-ink)" }}>
                    {t.label}
                  </p>
                  <p className="text-sm mt-1" style={{ color: "var(--color-ink)" }}>
                    {t.hypothesis}
                  </p>
                  {t.drawn_from && (
                    <p className="text-xs mt-1" style={{ color: "var(--color-faint)" }}>
                      Suggested by: {t.drawn_from} · a hypothesis, not a measurement
                    </p>
                  )}
                  <div className="mt-3">
                    <SageButton
                      onClick={() => onUseRequest(t.discover_request)}
                      disabled={handoffDisabled}
                      variant="secondary"
                    >
                      Find books like this
                    </SageButton>
                  </div>
                </div>
              ))}
            </>
          )}

          {handoffDisabled && (
            <p className="text-xs" style={{ color: "var(--color-faint)" }}>
              A scoring run is in flight on the Books tab — finish or stop it before
              sending it a new request.
            </p>
          )}

          {result.caution && (
            <p className="text-xs" style={{ color: "var(--color-muted)" }}>
              Caveat: {result.caution}
            </p>
          )}

          <div>
            <button
              onClick={() => setShowEvidence((v) => !v)}
              className="text-xs underline"
              style={{ color: "var(--color-muted)" }}
            >
              {showEvidence ? "Hide" : "Show"} the evidence this was argued from
            </button>
            {showEvidence && (
              <>
                <EvidenceTable rows={result.evidence} />
                <p className="text-xs mt-2" style={{ color: "var(--color-faint)" }}>
                  Affinity is your mean WA in that genre, shrunk toward your library mean
                  ({result.library.mean_wa?.toFixed(2)}) by{" "}
                  {result.library.shrinkage_k_books?.toFixed(1)} books of prior — so a
                  two-book genre can&apos;t out-rank a fifty-book one on noise. Surprise is
                  your actual rating minus what the engine predicted, over books you&apos;ve
                  finished.
                </p>
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ROOT PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════════════════ */

export default function GenrePredictClient() {
  const jobs = usePredictJobs();
  const router = useRouter();

  /** "Find books like this" — the hand-off to book prediction.
   *
   *  The request goes through the job provider rather than a query param
   *  because the provider is mounted above the router in the root layout, which
   *  is the whole reason a Predict run survives navigation; a request set here
   *  is simply there when the other page mounts. `pendingRequestFocus` is what
   *  stops the reader landing on a silently pre-filled box — the book page
   *  consumes it once and puts the cursor in the request.
   *
   *  Genre prediction reads the FICTION library, so this forces the kind too:
   *  landing on the nonfiction flow holding a fiction request is a dead end. */
  const useGenreRequest = useCallback(
    (request: string) => {
      jobs.setRequest("fiction", request);
      jobs.setActiveKind("fiction");
      jobs.requestFocusOnArrival();
      router.push("/predict");
    },
    [jobs, router],
  );

  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Genre Prediction
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          Which genres your own ratings actually favour — and where the engine has been
          wrong about you.
        </p>
      </div>

      <GenreRecommender
        // A fiction scoring run in flight would have its request overwritten by
        // the hand-off, so that button blocks. Asking for a recommendation is
        // read-only and never conflicts, so it does not.
        handoffDisabled={isRunBusy(jobs.runs.fiction)}
        state={jobs.genreTab}
        patch={jobs.patchGenreTab}
        onUseRequest={useGenreRequest}
      />
    </div>
  );
}
