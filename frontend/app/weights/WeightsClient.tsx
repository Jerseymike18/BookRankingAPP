"use client";

import { useMemo, useState } from "react";
import {
  setGenreWeights,
  setComponentWeights,
  resetWeights,
} from "@/lib/api";
import type { EffectiveWeights, BookKind } from "@/lib/types";

/* ── Working model ──────────────────────────────────────────────────────────
   Sliders operate on RELATIVE raw values (0–100 units); the displayed % is
   raw / sum, and the server normalizes to sum 1.0 on save. Effective weights
   already sum to 1.0, so ×100 seeds each slider at its own current percentage. */

type Group = {
  raw: Record<string, number>;
  saved: Record<string, number>;
  def: Record<string, number>;
  customized: boolean;
};
type CompGroup = Group & { category: string; components: string[] };
type GenreModel = {
  genre: string;
  catKeys: string[];
  cat: Group;
  comps: CompGroup[];
};

const toRaw = (m: Record<string, number>): Record<string, number> =>
  Object.fromEntries(Object.entries(m).map(([k, v]) => [k, v * 100]));

function buildModels(data: EffectiveWeights): GenreModel[] {
  return data.genres.map((g) => ({
    genre: g.genre,
    catKeys: data.categories,
    cat: {
      raw: toRaw(g.category_weights.effective),
      saved: toRaw(g.category_weights.effective),
      def: toRaw(g.category_weights.default),
      customized: g.category_weights.customized,
    },
    comps: g.categories.map((c) => ({
      category: c.category,
      components: c.components,
      raw: toRaw(c.effective),
      saved: toRaw(c.effective),
      def: toRaw(c.default),
      customized: c.customized,
    })),
  }));
}

const sum = (m: Record<string, number>) =>
  Object.values(m).reduce((a, b) => a + b, 0);
const pctOf = (m: Record<string, number>, k: string) => {
  const s = sum(m);
  return s > 0 ? (m[k] / s) * 100 : 0;
};
const isDirty = (g: Group) =>
  Object.keys(g.raw).some((k) => Math.abs(g.raw[k] - g.saved[k]) > 1e-6);

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

function CustomizedTag() {
  return (
    <span
      className="rounded px-1.5 py-0.5"
      style={{
        background: "var(--color-sage-light)",
        color: "var(--color-sage)",
        fontSize: "10px",
      }}
    >
      customized
    </span>
  );
}

function SliderRow({
  label,
  value,
  pct,
  onChange,
}: {
  label: string;
  value: number;
  pct: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-3 py-1">
      <div className="w-32 shrink-0 text-sm" style={{ color: "var(--color-ink)" }}>
        {label}
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1 min-w-0"
        style={{ accentColor: "var(--color-sage)" }}
      />
      <div
        className="w-14 shrink-0 text-right text-sm tabular-nums"
        style={{ color: "var(--color-muted)" }}
      >
        {pct.toFixed(1)}%
      </div>
    </div>
  );
}

function SaveButton({
  disabled,
  busy,
  onClick,
  label = "Save",
}: {
  disabled: boolean;
  busy: boolean;
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || busy}
      className="px-4 py-2 rounded-lg font-semibold text-sm disabled:opacity-40 transition-colors"
      style={{ background: "var(--color-sage)", color: "#fff" }}
    >
      {busy ? "Saving…" : label}
    </button>
  );
}

function LinkButton({
  onClick,
  children,
  disabled,
}: {
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="text-xs underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
      style={{ color: "var(--color-muted)" }}
    >
      {children}
    </button>
  );
}

export default function WeightsClient({
  initial,
  kind,
}: {
  initial: EffectiveWeights;
  kind: BookKind;
}) {
  const [models, setModels] = useState<GenreModel[]>(() => buildModels(initial));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const anyCustomized = useMemo(
    () => models.some((m) => m.cat.customized || m.comps.some((c) => c.customized)),
    [models]
  );
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? models.filter((m) => m.genre.toLowerCase().includes(q)) : models;
  }, [models, query]);

  function patchGenre(genre: string, fn: (m: GenreModel) => GenreModel) {
    setModels((ms) => ms.map((m) => (m.genre === genre ? fn(m) : m)));
  }
  function toggleExpanded(genre: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(genre)) next.delete(genre);
      else next.add(genre);
      return next;
    });
  }

  async function run(key: string, action: () => Promise<void>, ok: string) {
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(ok);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusyKey(null);
    }
  }

  const saveCat = (m: GenreModel) =>
    run(
      `cat:${m.genre}`,
      async () => {
        await setGenreWeights(m.genre, m.cat.raw, kind);
        patchGenre(m.genre, (x) => ({
          ...x,
          cat: { ...x.cat, saved: { ...x.cat.raw }, customized: true },
        }));
      },
      `Saved category weights for “${m.genre}”.`
    );

  const saveComp = (m: GenreModel, c: CompGroup) =>
    run(
      `comp:${m.genre}:${c.category}`,
      async () => {
        await setComponentWeights(m.genre, c.category, c.raw, kind);
        patchGenre(m.genre, (x) => ({
          ...x,
          comps: x.comps.map((g) =>
            g.category === c.category
              ? { ...g, saved: { ...g.raw }, customized: true }
              : g
          ),
        }));
      },
      `Saved ${c.category} component weights for “${m.genre}”.`
    );

  const resetGenre = (m: GenreModel) =>
    run(
      `genre:${m.genre}`,
      async () => {
        await resetWeights({ genre: m.genre }, kind);
        patchGenre(m.genre, (x) => ({
          ...x,
          cat: { ...x.cat, raw: { ...x.cat.def }, saved: { ...x.cat.def }, customized: false },
          comps: x.comps.map((g) => ({
            ...g,
            raw: { ...g.def },
            saved: { ...g.def },
            customized: false,
          })),
        }));
      },
      `Reset “${m.genre}” to defaults.`
    );

  const resetComp = (m: GenreModel, c: CompGroup) =>
    run(
      `comp:${m.genre}:${c.category}`,
      async () => {
        await resetWeights({ genre: m.genre, category: c.category }, kind);
        patchGenre(m.genre, (x) => ({
          ...x,
          comps: x.comps.map((g) =>
            g.category === c.category
              ? { ...g, raw: { ...g.def }, saved: { ...g.def }, customized: false }
              : g
          ),
        }));
      },
      `Reset ${c.category} components for “${m.genre}”.`
    );

  const resetAll = () =>
    run(
      "all",
      async () => {
        await resetWeights(undefined, kind);
        setModels((ms) =>
          ms.map((x) => ({
            ...x,
            cat: { ...x.cat, raw: { ...x.cat.def }, saved: { ...x.cat.def }, customized: false },
            comps: x.comps.map((g) => ({
              ...g,
              raw: { ...g.def },
              saved: { ...g.def },
              customized: false,
            })),
          }))
        );
      },
      "Reset all genres to default weights."
    );

  return (
    <div>
      {/* Reset-all (state-dependent, so it lives with the editor, not the header) */}
      {anyCustomized && (
        <div className="mb-4 flex justify-end">
          <button
            onClick={resetAll}
            disabled={busyKey === "all"}
            className="px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
            style={{ border: "1px solid var(--color-rule)", color: "var(--color-ink)" }}
          >
            {busyKey === "all" ? "Resetting…" : "Reset all to defaults"}
          </button>
        </div>
      )}

      {/* Feedback */}
      {error && (
        <div
          className="rounded-lg px-4 py-3 text-sm mb-4"
          style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          className="rounded-lg px-4 py-3 text-sm mb-4"
          style={{
            background: "var(--color-sage-light)",
            color: "var(--color-sage)",
            border: "1px solid var(--color-sage)",
          }}
        >
          {notice}
        </div>
      )}

      {/* Search */}
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter genres…"
        className="w-full max-w-xs px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 mb-5"
        style={inputStyle}
      />

      {/* Genre cards */}
      <div className="space-y-4">
        {visible.map((m) => {
          const catSum = sum(m.cat.raw);
          const catDirty = isDirty(m.cat);
          const open = expanded.has(m.genre);
          const genreCustomized =
            m.cat.customized || m.comps.some((c) => c.customized);
          return (
            <section
              key={m.genre}
              className="rounded-xl p-5"
              style={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-rule)",
              }}
            >
              {/* Card header */}
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="flex items-center gap-2 min-w-0">
                  <h2
                    className="font-display text-lg font-semibold truncate"
                    style={{ color: "var(--color-ink)" }}
                  >
                    {m.genre}
                  </h2>
                  {genreCustomized && <CustomizedTag />}
                </div>
                {genreCustomized && (
                  <LinkButton
                    onClick={() => resetGenre(m)}
                    disabled={busyKey === `genre:${m.genre}`}
                  >
                    {busyKey === `genre:${m.genre}` ? "Resetting…" : "Reset genre"}
                  </LinkButton>
                )}
              </div>

              {/* Category weights */}
              <p
                className="text-xs font-semibold uppercase tracking-widest mb-1"
                style={{ color: "var(--color-muted)" }}
              >
                Category weights
              </p>
              <div className="mb-2">
                {m.catKeys.map((cat) => (
                  <SliderRow
                    key={cat}
                    label={cat}
                    value={m.cat.raw[cat] ?? 0}
                    pct={pctOf(m.cat.raw, cat)}
                    onChange={(v) =>
                      patchGenre(m.genre, (x) => ({
                        ...x,
                        cat: { ...x.cat, raw: { ...x.cat.raw, [cat]: v } },
                      }))
                    }
                  />
                ))}
              </div>
              <div className="flex items-center gap-3">
                <SaveButton
                  disabled={!catDirty || catSum <= 0}
                  busy={busyKey === `cat:${m.genre}`}
                  onClick={() => saveCat(m)}
                />
                {catSum <= 0 && (
                  <span className="text-xs" style={{ color: "var(--color-spine-c)" }}>
                    At least one category must be above zero.
                  </span>
                )}
              </div>

              {/* Component weights (expandable) */}
              {m.comps.length > 0 && (
                <div className="mt-4 pt-3" style={{ borderTop: "1px solid var(--color-rule)" }}>
                  <button
                    onClick={() => toggleExpanded(m.genre)}
                    className="text-xs font-semibold uppercase tracking-widest flex items-center gap-1"
                    style={{ color: "var(--color-muted)" }}
                  >
                    <span>{open ? "▾" : "▸"}</span> Component weights
                  </button>
                  {open && (
                    <div className="mt-3 space-y-4">
                      {m.comps.map((c) => {
                        const cSum = sum(c.raw);
                        const cDirty = isDirty(c);
                        const cKey = `comp:${m.genre}:${c.category}`;
                        return (
                          <div key={c.category}>
                            <div className="flex items-center justify-between gap-2 mb-1">
                              <p
                                className="text-xs font-semibold"
                                style={{ color: "var(--color-ink)" }}
                              >
                                {c.category}
                                {c.customized && <span className="ml-2"><CustomizedTag /></span>}
                              </p>
                              {c.customized && (
                                <LinkButton
                                  onClick={() => resetComp(m, c)}
                                  disabled={busyKey === cKey}
                                >
                                  Reset
                                </LinkButton>
                              )}
                            </div>
                            {c.components.map((comp) => (
                              <SliderRow
                                key={comp}
                                label={comp}
                                value={c.raw[comp] ?? 0}
                                pct={pctOf(c.raw, comp)}
                                onChange={(v) =>
                                  patchGenre(m.genre, (x) => ({
                                    ...x,
                                    comps: x.comps.map((g) =>
                                      g.category === c.category
                                        ? { ...g, raw: { ...g.raw, [comp]: v } }
                                        : g
                                    ),
                                  }))
                                }
                              />
                            ))}
                            <div className="mt-1">
                              <SaveButton
                                disabled={!cDirty || cSum <= 0}
                                busy={busyKey === cKey}
                                onClick={() => saveComp(m, c)}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </section>
          );
        })}
        {visible.length === 0 && (
          <p className="text-sm py-8 text-center" style={{ color: "var(--color-muted)" }}>
            No genres match “{query}”.
          </p>
        )}
      </div>
    </div>
  );
}
