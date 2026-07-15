"use client";

import { useState } from "react";
import { setGenreWeights } from "@/lib/api";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import type { EffectiveWeights } from "@/lib/types";

/* ── First-run tutorial + simplified genre-weight picker ─────────────────────
   A new account lands here (the proxy sends any not-yet-onboarded user to
   /welcome). The tour is static; the setup step lets them KEEP the defaults in
   one click, or CUSTOMIZE the five category weights for a few common genres.
   Either way finishing sets the Supabase `onboarded` flag so they aren't sent
   back here again — full control of every genre (and the components within each)
   lives on the /weights page, reachable later from the nav.

   Weights are RELATIVE (typed numbers); the shown % is raw/sum and the server
   normalizes to sum 1.0 on save — the same model as the /weights editor, kept
   deliberately in sync with it. */

// Auth is configured only on the hosted multi-tenant build; there we persist the
// onboarded flag onto the Supabase user. Local dev / static builds leave the env
// unset, so we skip the metadata write and just continue.
const AUTH_CONFIGURED =
  !!process.env.NEXT_PUBLIC_SUPABASE_URL &&
  !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

// A small, recognizable starter set with deliberately distinct default profiles
// (Story-led, Character/Theme-led, Theme-heavy, realist-no-worldbuilding) so a
// new reader can see at a glance that weighting varies by genre. Intersected with
// the live payload, so a renamed/removed genre simply drops out of the tour.
const STARTER_GENRES = [
  "Epic Fantasy",
  "Literary Fiction",
  "Science Fiction (Hard)",
  "Historical Fiction",
];

// Book-length preference → a cold-start "length slope" the engine applies to unread
// books by authors you haven't read yet, before it has enough of your ratings to learn
// this itself. Values are the slope (WA per 10× word count); anchored on the seed
// reader's fitted slope (~1.0) at the extremes. 0 = no adjustment. Saved to the
// Supabase user's metadata as `word_count_pref` and read back by the prediction API.
const LENGTH_OPTIONS: { label: string; value: number }[] = [
  { label: "Short & tight", value: -1 },
  { label: "Lean shorter", value: -0.5 },
  { label: "No preference", value: 0 },
  { label: "Lean longer", value: 0.5 },
  { label: "Long epics", value: 1 },
];

type GenreModel = {
  genre: string;
  cats: string[]; // category order (Story/Character/Theme/Aesthetics/Worldbuilding)
  raw: Record<string, string>; // editable relative values, as strings
  def: Record<string, string>; // pristine defaults (for the read-only preview)
};

const fmt = (v: number) => String(+(v * 100).toFixed(2));
const toRaw = (m: Record<string, number>): Record<string, string> =>
  Object.fromEntries(Object.entries(m).map(([k, v]) => [k, fmt(v)]));
const numOf = (s: string) => {
  const v = parseFloat(s);
  return Number.isFinite(v) && v >= 0 ? v : 0;
};
const sumOf = (m: Record<string, string>) =>
  Object.values(m).reduce((a, s) => a + numOf(s), 0);
const pctOf = (m: Record<string, string>, k: string) => {
  const s = sumOf(m);
  return s > 0 ? (numOf(m[k]) / s) * 100 : 0;
};
const toNums = (m: Record<string, string>): Record<string, number> =>
  Object.fromEntries(Object.keys(m).map((k) => [k, numOf(m[k])]));
const isDirty = (m: GenreModel) =>
  m.cats.some((k) => numOf(m.raw[k]) !== numOf(m.def[k]));

const NUM_RE = /^\d*\.?\d*$/; // allow "", "0", "0.", "12.5" while typing

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

function buildModels(data: EffectiveWeights): GenreModel[] {
  const order = new Map(STARTER_GENRES.map((g, i) => [g, i] as const));
  const globals = data.genres.filter((g) => !g.custom);
  const picked = globals.filter((g) => order.has(g.genre));
  // Fall back to the first few global genres if the curated names ever change.
  const list = (picked.length ? picked : globals.slice(0, 4)).slice();
  list.sort((a, b) => (order.get(a.genre) ?? 99) - (order.get(b.genre) ?? 99));
  return list.map((g) => ({
    genre: g.genre,
    cats: data.categories,
    raw: toRaw(g.category_weights.default),
    def: toRaw(g.category_weights.default),
  }));
}

function LocationTag({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 font-medium whitespace-nowrap"
      style={{
        background: "var(--color-sage-light)",
        color: "var(--color-sage)",
        fontSize: "11px",
      }}
    >
      {children}
    </span>
  );
}

function Section({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className="rounded-xl p-5 mb-4"
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      <p
        className="text-xs font-semibold uppercase tracking-widest mb-1"
        style={{ color: "var(--color-muted)" }}
      >
        {eyebrow}
      </p>
      <h2
        className="font-display text-lg font-semibold mb-2"
        style={{ color: "var(--color-ink)" }}
      >
        {title}
      </h2>
      <div className="text-sm leading-relaxed" style={{ color: "var(--color-ink)" }}>
        {children}
      </div>
    </section>
  );
}

function NumberRow({
  label,
  value,
  pct,
  onChange,
}: {
  label: string;
  value: string;
  pct: number;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-3 py-1">
      <div className="w-32 shrink-0 text-sm" style={{ color: "var(--color-ink)" }}>
        {label}
      </div>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        onChange={(e) => {
          const r = e.target.value;
          if (r === "" || NUM_RE.test(r)) onChange(r);
        }}
        className="w-24 px-2 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
        style={inputStyle}
      />
      <div
        className="w-16 shrink-0 text-sm tabular-nums"
        style={{ color: "var(--color-muted)" }}
      >
        = {pct.toFixed(1)}%
      </div>
    </div>
  );
}

export default function WelcomeClient({ weights }: { weights: EffectiveWeights }) {
  const [models, setModels] = useState<GenreModel[]>(() => buildModels(weights));
  const [mode, setMode] = useState<"keep" | "customize">("keep");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lengthPref, setLengthPref] = useState<number>(0);
  const [favAuthors, setFavAuthors] = useState<string[]>(["", "", "", "", ""]);

  function patch(genre: string, cat: string, v: string) {
    setModels((ms) =>
      ms.map((m) => (m.genre === genre ? { ...m, raw: { ...m.raw, [cat]: v } } : m))
    );
  }

  // A dirty genre with no positive weight can't be saved (the server rejects a
  // zero sum). Unchanged genres are never written, so they never block.
  const blocked = models.some((m) => isDirty(m) && sumOf(m.raw) <= 0);

  async function finish(persist: boolean) {
    setError(null);
    if (persist) {
      const bad = models.find((m) => isDirty(m) && sumOf(m.raw) <= 0);
      if (bad) {
        setError(
          `Give “${bad.genre}” at least one category above zero, or keep its defaults.`
        );
        return;
      }
    }
    setBusy(true);
    try {
      // Persist only the genres the reader actually changed. Untouched genres are
      // left on the shared defaults — a valid state, never null/global-by-accident.
      if (persist) {
        for (const m of models) {
          if (isDirty(m)) await setGenreWeights(m.genre, toNums(m.raw), "fiction");
        }
      }
      // Mark onboarding complete so the proxy stops routing here (hosted only).
      if (AUTH_CONFIGURED) {
        const supabase = createSupabaseBrowserClient();
        const { error: metaErr } = await supabase.auth.updateUser({
          data: {
            onboarded: true,
            word_count_pref: lengthPref,
            fav_authors: favAuthors.map((s) => s.trim()).filter(Boolean),
          },
        });
        if (metaErr) throw new Error(metaErr.message);
      }
      // Hard navigation so the proxy re-runs against the refreshed session and
      // sends the now-onboarded reader on into the app instead of back here.
      window.location.assign("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong. Please try again.");
      setBusy(false);
    }
  }

  const customizing = mode === "customize";

  return (
    <div>
      {/* Hero */}
      <div className="mb-6">
        <p
          className="text-xs font-semibold uppercase tracking-widest mb-2"
          style={{ color: "var(--color-sage)" }}
        >
          Welcome
        </p>
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Your reading ledger
        </h1>
        <p
          className="mt-2 text-sm max-w-2xl leading-relaxed"
          style={{ color: "var(--color-muted)" }}
        >
          A quick tour, then one setup step. About a minute — you can change
          everything later.
        </p>
      </div>

      {/* Phase 1 — static walkthrough */}
      <Section eyebrow="Step 1" title="What this is">
        <p>
          A personal book-rating and prediction system. You rate every book you
          read on 14 fine-grained components — grouped into five categories:{" "}
          <strong>Story</strong>, <strong>Character</strong>,{" "}
          <strong>Aesthetics</strong>, <strong>Theme</strong>, and{" "}
          <strong>Worldbuilding</strong>{" "}— each scored 0–10. From those ratings the
          engine learns your taste and predicts how much you&rsquo;ll enjoy a book
          you haven&rsquo;t read yet, before you open it.
        </p>
      </Section>

      <Section eyebrow="Step 2" title="The three things you’ll do">
        <ul className="space-y-3">
          <li>
            <p className="font-semibold" style={{ color: "var(--color-ink)" }}>
              Log a book you&rsquo;ve read
            </p>
            <p style={{ color: "var(--color-muted)" }}>
              Enter its scores across the components; it drops into your rankings.{" "}
              <LocationTag>Predictions ▸ Add a Book</LocationTag>
            </p>
          </li>
          <li>
            <p className="font-semibold" style={{ color: "var(--color-ink)" }}>
              Predict an unread book
            </p>
            <p style={{ color: "var(--color-muted)" }}>
              Give a title, author, and genre; the engine estimates every component,
              a weighted score, and a predicted rank — with a calibrated range.{" "}
              <LocationTag>Predictions ▸ Predict</LocationTag>
            </p>
          </li>
          <li>
            <p className="font-semibold" style={{ color: "var(--color-ink)" }}>
              See what to read next
            </p>
            <p style={{ color: "var(--color-muted)" }}>
              Your predicted books rank alongside the ones you&rsquo;ve read, so the
              queue is ordered by expected enjoyment.{" "}
              <LocationTag>Predictions ▸ Read Queue</LocationTag>{" "}·{" "}
              <LocationTag>Fiction ▸ Rankings</LocationTag>
            </p>
          </li>
        </ul>
      </Section>

      <Section eyebrow="Step 3" title="How the predictions work">
        <p>
          Each book&rsquo;s scores flow through a genre-weighted schema — the same
          weights you&rsquo;re about to set — to a single weighted score the whole
          library sorts by. The engine is checked with{" "}
          <em>walk-forward validation</em>: for every book it re-predicts using only
          what was known before you read it, so the accuracy you see is honest rather
          than hindsight. The details live under{" "}
          <LocationTag>More ▸ Methodology</LocationTag>{" "}and{" "}
          <LocationTag>More ▸ Track Record</LocationTag>.
        </p>
      </Section>

      {/* Phase 2 — interactive genre-weight picker */}
      <section
        className="rounded-xl p-5 mb-4"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-sage)" }}
      >
        <p
          className="text-xs font-semibold uppercase tracking-widest mb-1"
          style={{ color: "var(--color-sage)" }}
        >
          Step 4 · Set up
        </p>
        <h2
          className="font-display text-lg font-semibold mb-2"
          style={{ color: "var(--color-ink)" }}
        >
          Set your preferences
        </h2>

        {/* Book-length preference → cold-start length slope. Helps predict brand-new
            authors before the engine has enough of your ratings to learn this itself. */}
        <div
          className="rounded-lg p-4 mb-5"
          style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}
        >
          <h3 className="font-display font-semibold mb-1" style={{ color: "var(--color-ink)" }}>
            How long do you like your books?
          </h3>
          <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
            Until you&rsquo;ve logged enough books for the engine to learn this from your
            ratings, your answer sharpens predictions for authors you&rsquo;ve never read.
          </p>
          <div className="flex flex-wrap gap-2">
            {LENGTH_OPTIONS.map((o) => {
              const on = lengthPref === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => setLengthPref(o.value)}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                  style={
                    on
                      ? {
                          background: "var(--color-sage)",
                          color: "#fff",
                          border: "1px solid var(--color-sage)",
                        }
                      : {
                          background: "transparent",
                          color: "var(--color-ink)",
                          border: "1px solid var(--color-rule)",
                        }
                  }
                >
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Favorite authors → cold-start author prior. Books by these (and LLM-found
            similar authors) get a nudge until the engine has seen you rate them. */}
        <div
          className="rounded-lg p-4 mb-5"
          style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-rule)" }}
        >
          <h3 className="font-display font-semibold mb-1" style={{ color: "var(--color-ink)" }}>
            Your favorite authors
          </h3>
          <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
            Up to five. Until you&rsquo;ve rated them here, predictions for their books
            &mdash; and stylistically similar authors &mdash; get nudged toward your taste.
          </p>
          <div className="space-y-2">
            {favAuthors.map((v, i) => (
              <input
                key={i}
                type="text"
                value={v}
                onChange={(e) =>
                  setFavAuthors((a) => a.map((x, j) => (j === i ? e.target.value : x)))
                }
                placeholder={`Author ${i + 1}`}
                className="w-full px-3 py-1.5 rounded-lg text-sm border focus:outline-none focus:ring-2"
                style={inputStyle}
              />
            ))}
          </div>
        </div>

        <p className="text-sm leading-relaxed mb-4" style={{ color: "var(--color-ink)" }}>
          Different genres reward different things — a thriller leans on Story, a
          literary novel on Character and Theme. Below are the defaults for a few
          common genres. Keep them as your starting point, or tune any to your
          taste. Values are relative; they&rsquo;re normalized to 100% when saved.
        </p>

        <div className="space-y-3">
          {models.map((m) => {
            const sum = sumOf(m.raw);
            const dirty = isDirty(m);
            return (
              <div
                key={m.genre}
                className="rounded-lg p-4"
                style={{
                  background: "var(--color-surface-2)",
                  border: "1px solid var(--color-rule)",
                }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <h3
                    className="font-display font-semibold"
                    style={{ color: "var(--color-ink)" }}
                  >
                    {m.genre}
                  </h3>
                  {customizing && dirty && (
                    <span
                      className="rounded px-1.5 py-0.5"
                      style={{
                        background: "var(--color-sage-light)",
                        color: "var(--color-sage)",
                        fontSize: "10px",
                      }}
                    >
                      edited
                    </span>
                  )}
                </div>

                {customizing ? (
                  <div>
                    {m.cats.map((c) => (
                      <NumberRow
                        key={c}
                        label={c}
                        value={m.raw[c] ?? ""}
                        pct={pctOf(m.raw, c)}
                        onChange={(v) => patch(m.genre, c, v)}
                      />
                    ))}
                    {sum <= 0 && (
                      <p className="text-xs mt-1" style={{ color: "var(--color-spine-c)" }}>
                        At least one category must be above zero.
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-x-5 gap-y-1">
                    {m.cats.map((c) => (
                      <span key={c} className="text-sm" style={{ color: "var(--color-muted)" }}>
                        {c}{" "}
                        <span className="tabular-nums font-medium" style={{ color: "var(--color-ink)" }}>
                          {pctOf(m.def, c).toFixed(1)}%
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <p className="text-xs mt-4" style={{ color: "var(--color-faint)" }}>
          This is just a head start. You can tailor all genres — and the individual
          components within each category, for fiction and nonfiction — any time
          under More ▸ Genre Weights.
        </p>

        {error && (
          <div
            className="rounded-lg px-4 py-3 text-sm mt-4"
            style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}
          >
            {error}
          </div>
        )}

        {/* Actions — two modes */}
        <div className="flex flex-wrap items-center justify-between gap-3 mt-5">
          {customizing ? (
            <>
              <button
                onClick={() => finish(false)}
                disabled={busy}
                className="text-sm underline underline-offset-2 disabled:opacity-40"
                style={{ color: "var(--color-muted)" }}
              >
                Keep the defaults instead
              </button>
              <button
                onClick={() => finish(true)}
                disabled={busy || blocked}
                className="px-5 py-2.5 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                {busy ? "Saving…" : "Save my weights & continue →"}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => {
                  setMode("customize");
                  setError(null);
                }}
                disabled={busy}
                className="px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
                style={{ border: "1px solid var(--color-rule)", color: "var(--color-ink)" }}
              >
                Customize per genre
              </button>
              <button
                onClick={() => finish(false)}
                disabled={busy}
                className="px-5 py-2.5 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                {busy ? "Setting up…" : "Keep the defaults & continue →"}
              </button>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
