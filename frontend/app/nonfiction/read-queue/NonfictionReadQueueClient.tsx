"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import type { NonfictionReadQueueResponse, NonfictionRecommendation } from "@/lib/types";
import {
  saveNonfictionQueue,
  setNonfictionDone,
  deleteNonfictionRecommendation,
} from "@/lib/api";
import { READONLY } from "@/lib/readonly";

function fmtWords(w: number | null) {
  if (!w) return null;
  if (w >= 1_000_000) return `${(w / 1_000_000).toFixed(1)}M words`;
  if (w >= 1_000) return `${Math.round(w / 1_000)}K words`;
  return `${w} words`;
}

/* A compact recommendation row with its predicted Total Average + actions. */
function RecRow({
  rec,
  position,
  controls,
}: {
  rec: NonfictionRecommendation;
  position?: number;
  controls: React.ReactNode;
}) {
  return (
    <div
      className="flex items-start gap-3 px-4 py-3"
      style={{ borderTop: "1px solid var(--color-rule)" }}
    >
      {position != null && (
        <span className="font-display italic text-sm mt-0.5" style={{ color: "var(--color-faint)", minWidth: "1.5rem" }}>
          {position}
        </span>
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-display font-semibold text-sm" style={{ color: "var(--color-ink)" }}>
            {rec.title}
          </span>
          <span className="text-xs" style={{ color: "var(--color-muted)" }}>{rec.author}</span>
          {fmtWords(rec.words) && (
            <span className="text-xs" style={{ color: "var(--color-faint)" }}>· {fmtWords(rec.words)}</span>
          )}
        </div>
        {rec.blurb && (
          <p className="text-xs mt-1 italic" style={{ color: "var(--color-muted)" }}>{rec.blurb}</p>
        )}
        <div className="flex items-center gap-3 mt-1.5 text-xs" style={{ color: "var(--color-muted)" }}>
          <span>Total Avg <b style={{ color: "var(--color-sage)" }}>{rec.total_average?.toFixed(2) ?? "—"}</b></span>
          <span>WA {rec.wa?.toFixed(2) ?? "—"}</span>
          {rec.predicted_rank != null && <span>predicted rank ~{rec.predicted_rank}</span>}
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">{controls}</div>
    </div>
  );
}

function MiniButton({
  onClick, children, danger, disabled,
}: {
  onClick: () => void; children: React.ReactNode; danger?: boolean; disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="px-2.5 py-1 rounded-md text-xs font-medium transition-colors disabled:opacity-40"
      style={{
        background: "var(--color-surface)",
        color: danger ? "#DC2626" : "var(--color-sage)",
        border: `1px solid ${danger ? "#FCA5A5" : "var(--color-rule)"}`,
      }}
    >
      {children}
    </button>
  );
}

export default function NonfictionReadQueueClient({
  data,
  initialQueue,
}: {
  data: NonfictionReadQueueResponse;
  initialQueue: string[];
}) {
  const router = useRouter();
  const recs = data.recommendations;
  // Only keep queued titles that still exist as not-done recommendations.
  const known = new Set(recs.map((r) => r.title));
  const [queue, setQueue] = useState<string[]>(initialQueue.filter((t) => known.has(t)));
  const [busy, setBusy] = useState(false);

  const persistQueue = useCallback(async (next: string[]) => {
    setQueue(next);
    setBusy(true);
    try {
      await saveNonfictionQueue(next);
    } finally {
      setBusy(false);
    }
  }, []);

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= queue.length) return;
    const next = [...queue];
    [next[i], next[j]] = [next[j], next[i]];
    persistQueue(next);
  };

  async function markRead(title: string) {
    setBusy(true);
    try {
      await setNonfictionDone(title, true);
      const next = queue.filter((t) => t !== title);
      if (next.length !== queue.length) await saveNonfictionQueue(next);
      setQueue(next);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  async function remove(title: string) {
    setBusy(true);
    try {
      await deleteNonfictionRecommendation(title);
      const next = queue.filter((t) => t !== title);
      if (next.length !== queue.length) await saveNonfictionQueue(next);
      setQueue(next);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  const byTitle = Object.fromEntries(recs.map((r) => [r.title, r]));
  const queuedSet = new Set(queue);
  const unqueued = recs.filter((r) => !queuedSet.has(r.title));

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-bold leading-tight" style={{ color: "var(--color-ink)" }}>
          Nonfiction Read Queue
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {recs.length} on the nonfiction TBR · ranked by predicted Total Average. Save books from the
          Predict page.
        </p>
      </div>

      {recs.length === 0 && (
        <div className="rounded-xl p-8 text-center" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            Nothing on the nonfiction TBR yet. Head to{" "}
            <a href="/predict" className="underline" style={{ color: "var(--color-sage)" }}>Predict</a>{" "}
            (Nonfiction), research a book, and Save to TBR.
          </p>
        </div>
      )}

      {/* Reading queue (ordered) */}
      {queue.length > 0 && (
        <section className="rounded-xl overflow-hidden mb-8" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
          <div className="px-4 py-2.5" style={{ background: "var(--color-surface-2)" }}>
            <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
              Up next ({queue.length})
            </span>
          </div>
          {queue.map((title, i) => {
            const rec = byTitle[title];
            if (!rec) return null;
            return (
              <RecRow
                key={title}
                rec={rec}
                position={i + 1}
                controls={
                  READONLY ? null : (
                  <>
                    <MiniButton onClick={() => move(i, -1)} disabled={busy || i === 0}>↑</MiniButton>
                    <MiniButton onClick={() => move(i, 1)} disabled={busy || i === queue.length - 1}>↓</MiniButton>
                    <MiniButton onClick={() => persistQueue(queue.filter((t) => t !== title))} disabled={busy}>Remove</MiniButton>
                    <MiniButton onClick={() => markRead(title)} disabled={busy}>Mark read</MiniButton>
                  </>
                  )
                }
              />
            );
          })}
        </section>
      )}

      {/* TBR (not queued) */}
      {unqueued.length > 0 && (
        <section className="rounded-xl overflow-hidden" style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}>
          <div className="px-4 py-2.5" style={{ background: "var(--color-surface-2)" }}>
            <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--color-muted)" }}>
              To be read ({unqueued.length})
            </span>
          </div>
          {unqueued.map((rec) => (
            <RecRow
              key={rec.title}
              rec={rec}
              controls={
                READONLY ? null : (
                <>
                  <MiniButton onClick={() => persistQueue([...queue, rec.title])} disabled={busy}>Add to queue</MiniButton>
                  <MiniButton onClick={() => markRead(rec.title)} disabled={busy}>Mark read</MiniButton>
                  <MiniButton onClick={() => remove(rec.title)} disabled={busy} danger>Remove</MiniButton>
                </>
                )
              }
            />
          ))}
        </section>
      )}
    </div>
  );
}
