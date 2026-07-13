"use client";

import { useMemo, useState } from "react";
import {
  setGenreWeights,
  setComponentWeights,
  resetWeights,
  addGenre,
  deleteGenre,
  fetchWeights,
} from "@/lib/api";
import type { EffectiveWeights, BookKind } from "@/lib/types";

/* ── Working model ──────────────────────────────────────────────────────────
   Weights are TYPED (not dragged). Each box holds a raw STRING (so it can be
   empty/partial while typing) interpreted as a RELATIVE weight; the shown % is
   raw / sum, and the server normalizes to sum 1.0 on save. */

type Group = {
  raw: Record<string, string>;
  saved: Record<string, string>;
  def: Record<string, string>;
  customized: boolean;
};
type CompGroup = Group & { category: string; components: string[] };
type GenreModel = {
  genre: string;
  custom: boolean;
  catKeys: string[];
  cat: Group;
  comps: CompGroup[];
};

const fmt = (v: number) => String(+(v * 100).toFixed(2));
const toRaw = (m: Record<string, number>): Record<string, string> =>
  Object.fromEntries(Object.entries(m).map(([k, v]) => [k, fmt(v)]));

function buildModels(data: EffectiveWeights): GenreModel[] {
  return data.genres.map((g) => ({
    genre: g.genre,
    custom: g.custom,
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
const isDirty = (g: Group) =>
  Object.keys(g.raw).some((k) => numOf(g.raw[k]) !== numOf(g.saved[k]));

const NUM_RE = /^\d*\.?\d*$/; // allow "", "0", "0.", "12.5" while typing

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

function Tag({ label, tone = "sage" }: { label: string; tone?: "sage" | "muted" }) {
  const c =
    tone === "sage"
      ? { background: "var(--color-sage-light)", color: "var(--color-sage)" }
      : { background: "var(--color-surface-2)", color: "var(--color-muted)" };
  return (
    <span className="rounded px-1.5 py-0.5" style={{ ...c, fontSize: "10px" }}>
      {label}
    </span>
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
  danger,
}: {
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="text-xs underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
      style={{ color: danger ? "var(--color-spine-c)" : "var(--color-muted)" }}
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

  // Add-genre form
  const [adding, setAdding] = useState(false);
  const emptyNew = () =>
    Object.fromEntries(initial.categories.map((c) => [c, "1"])) as Record<string, string>;
  const [newName, setNewName] = useState("");
  const [newWeights, setNewWeights] = useState<Record<string, string>>(emptyNew);

  // "Reset all" only affects global-genre customizations (custom genres are kept).
  const anyGlobalCustomized = useMemo(
    () =>
      models.some(
        (m) => !m.custom && (m.cat.customized || m.comps.some((c) => c.customized))
      ),
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

  // After a structural change (add/delete genre) re-seed from the server so the
  // genre list + custom flags stay authoritative.
  async function refetch() {
    setModels(buildModels(await fetchWeights(kind)));
  }

  const saveCat = (m: GenreModel) =>
    run(
      `cat:${m.genre}`,
      async () => {
        await setGenreWeights(m.genre, toNums(m.cat.raw), kind);
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
        await setComponentWeights(m.genre, c.category, toNums(c.raw), kind);
        patchGenre(m.genre, (x) => ({
          ...x,
          comps: x.comps.map((g) =>
            g.category === c.category ? { ...g, saved: { ...g.raw }, customized: true } : g
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
        await refetch();
      },
      "Reset all genres to default weights."
    );

  const removeGenre = (m: GenreModel) =>
    run(
      `del:${m.genre}`,
      async () => {
        await deleteGenre(m.genre, kind);
        await refetch();
      },
      `Deleted “${m.genre}”.`
    );

  const submitAddGenre = () => {
    const name = newName.trim();
    if (!name) {
      setError("Enter a name for the new genre.");
      return;
    }
    if (sumOf(newWeights) <= 0) {
      setError("At least one category weight must be above zero.");
      return;
    }
    return run(
      "add-genre",
      async () => {
        await addGenre(name, toNums(newWeights), kind);
        await refetch();
        setNewName("");
        setNewWeights(emptyNew());
        setAdding(false);
      },
      `Added “${name}”.`
    );
  };

  const trackNoun = kind === "nonfiction" ? "nonfiction genre" : "genre";

  return (
    <div>
      {/* Top actions */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <button
          onClick={() => {
            setAdding((v) => !v);
            setError(null);
          }}
          className="px-3 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{ background: "var(--color-sage-light)", color: "var(--color-sage)" }}
        >
          {adding ? "Cancel" : `＋ Add a ${trackNoun}`}
        </button>
        {anyGlobalCustomized && (
          <button
            onClick={resetAll}
            disabled={busyKey === "all"}
            className="px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
            style={{ border: "1px solid var(--color-rule)", color: "var(--color-ink)" }}
          >
            {busyKey === "all" ? "Resetting…" : "Reset all to defaults"}
          </button>
        )}
      </div>

      {/* Add-genre form */}
      {adding && (
        <section
          className="rounded-xl p-5 mb-4"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-sage)" }}
        >
          <label
            className="block text-xs font-semibold uppercase tracking-widest mb-1"
            style={{ color: "var(--color-muted)" }}
          >
            New {trackNoun} name
          </label>
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. Grimdark Fantasy"
            className="w-full max-w-sm px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2 mb-3"
            style={inputStyle}
          />
          <p
            className="text-xs font-semibold uppercase tracking-widest mb-1"
            style={{ color: "var(--color-muted)" }}
          >
            Category weights
          </p>
          <div className="mb-3">
            {initial.categories.map((cat) => (
              <NumberRow
                key={cat}
                label={cat}
                value={newWeights[cat] ?? ""}
                pct={pctOf(newWeights, cat)}
                onChange={(v) => setNewWeights((w) => ({ ...w, [cat]: v }))}
              />
            ))}
          </div>
          <p className="text-xs mb-3" style={{ color: "var(--color-faint)" }}>
            Component weights start equal within each category — tune them after the genre is
            created. Books can then be tagged with this genre.
          </p>
          <SaveButton
            disabled={!newName.trim() || sumOf(newWeights) <= 0}
            busy={busyKey === "add-genre"}
            onClick={submitAddGenre}
            label={`Create ${trackNoun}`}
          />
        </section>
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
          const catSum = sumOf(m.cat.raw);
          const catDirty = isDirty(m.cat);
          const open = expanded.has(m.genre);
          const globalCustomized =
            !m.custom && (m.cat.customized || m.comps.some((c) => c.customized));
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
                  {m.custom ? (
                    <Tag label="private" />
                  ) : globalCustomized ? (
                    <Tag label="customized" />
                  ) : null}
                </div>
                {m.custom ? (
                  <LinkButton
                    onClick={() => removeGenre(m)}
                    disabled={busyKey === `del:${m.genre}`}
                    danger
                  >
                    {busyKey === `del:${m.genre}` ? "Deleting…" : "Delete genre"}
                  </LinkButton>
                ) : (
                  globalCustomized && (
                    <LinkButton
                      onClick={() => resetGenre(m)}
                      disabled={busyKey === `genre:${m.genre}`}
                    >
                      {busyKey === `genre:${m.genre}` ? "Resetting…" : "Reset genre"}
                    </LinkButton>
                  )
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
                  <NumberRow
                    key={cat}
                    label={cat}
                    value={m.cat.raw[cat] ?? ""}
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
                        const cSum = sumOf(c.raw);
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
                                {c.customized && !m.custom && (
                                  <span className="ml-2">
                                    <Tag label="customized" />
                                  </span>
                                )}
                              </p>
                              {c.customized && !m.custom && (
                                <LinkButton
                                  onClick={() => resetComp(m, c)}
                                  disabled={busyKey === cKey}
                                >
                                  Reset
                                </LinkButton>
                              )}
                            </div>
                            {c.components.map((comp) => (
                              <NumberRow
                                key={comp}
                                label={comp}
                                value={c.raw[comp] ?? ""}
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
