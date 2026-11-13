"use client";

import { useState } from "react";
import Link from "next/link";
import type { ReadingStatusResponse, StatusSlot, BookKind } from "@/lib/types";
import { seriesLabel } from "@/lib/format";
import { TypeToggle, type TypeScope } from "@/components/TypeToggle";
import { FinishedMidBookReviews, MidBookReviews } from "@/components/MidBookReviews";
import { useReadOnly } from "@/lib/readonly-context";

/* ── Status tab ───────────────────────────────────────────────────────────── */

const CATEGORY_ORDER_BY_KIND: Record<BookKind, string[]> = {
  fiction: ["Story", "Character", "Aesthetics", "Theme", "Worldbuilding"],
  nonfiction: ["Quality", "Aesthetics", "Theme"],
};

function SlotCard({
  label,
  slot,
  predicted,
  kind,
}: {
  label: string;
  slot: StatusSlot | null;
  predicted: boolean;
  kind: BookKind;
}) {
  const cardStyle = {
    background: "var(--color-surface)",
    boxShadow: "var(--shadow-card)",
  };

  if (!slot) {
    return (
      <div className="rounded-xl p-5 flex flex-col gap-2" style={cardStyle}>
        <span
          className="text-xs font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-muted)" }}
        >
          {label}
        </span>
        <p className="text-sm mt-2" style={{ color: "var(--color-faint)" }}>
          —
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl p-5 flex flex-col gap-3" style={cardStyle}>
      {/* Label row */}
      <div className="flex items-center gap-2">
        <span
          className="text-xs font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-muted)" }}
        >
          {label}
        </span>
        {predicted && (
          <span
            className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{
              background: "var(--color-surface-2)",
              color: "var(--color-sage)",
              border: "1px solid var(--color-rule)",
            }}
          >
            predicted
          </span>
        )}
      </div>

      {/* Title / meta */}
      <div>
        <p
          className="font-display font-bold text-lg leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          {slot.title}
        </p>
        <p className="text-sm mt-0.5" style={{ color: "var(--color-muted)" }}>
          {[slot.author, slot.genre].filter(Boolean).join(" · ")}
        </p>
        {slot.series && (
          <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>
            {seriesLabel(slot.series, slot.series_number)}
          </p>
        )}
      </div>

      {/* Score section */}
      {slot.has_prediction === false && slot.wa === null ? (
        <p className="text-sm" style={{ color: "var(--color-faint)" }}>
          No prediction yet — visit the{" "}
          <Link
            href="/predict"
            className="underline"
            style={{ color: "var(--color-sage)" }}
          >
            Predict page
          </Link>{" "}
          to research this book.
        </p>
      ) : (
        <>
          {/* WA badge + rank */}
          <div className="flex items-center gap-3">
            <span className="wa-badge">{slot.wa?.toFixed(2)}</span>
            {slot.rank != null && (
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                rank {slot.rank} of {slot.total}
              </span>
            )}
          </div>

          {/* Category averages */}
          {Object.keys(slot.category_avgs).length > 0 && (
            <div className="grid grid-cols-3 gap-1.5 mt-1">
              {CATEGORY_ORDER_BY_KIND[kind].filter((cat) => cat in slot.category_avgs).map((cat) => (
                <div key={cat} className="comp-tile">
                  <span className="comp-label">{cat}</span>
                  <span className="comp-value">
                    {slot.category_avgs[cat].toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatusTab({ status, kind }: { status: ReadingStatusResponse; kind: BookKind }) {
  // Checkpoints are a private journal: hidden on read-only deploys and on
  // another reader's public profile (a write there would hit the VIEWER's account).
  const readOnly = useReadOnly();
  return (
    <div className="grid sm:grid-cols-3 gap-4">
      <SlotCard label="Last Read" slot={status.last_read} predicted={false} kind={kind} />
      <SlotCard
        label="Currently Reading"
        slot={status.currently_reading}
        predicted={true}
        kind={kind}
      />
      <SlotCard label="Reading Next" slot={status.reading_next} predicted={true} kind={kind} />
      {status.currently_reading && !readOnly && (
        <MidBookReviews slot={status.currently_reading} kind={kind} />
      )}
      {!readOnly && <FinishedMidBookReviews kind={kind} />}
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────────────────────── */

/* The page is reading STATUS only — last read, current read, next up. The
   library numbers it used to repeat live once each elsewhere: totals on Stats,
   per-year on Timeline, per-genre and per-author in Taste Lab. */
function ReadingSingle({ status, kind = "fiction" }: { status: ReadingStatusResponse; kind?: BookKind }) {
  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Reading
        </h1>
      </div>
      <StatusTab status={status} kind={kind} />
    </div>
  );
}

/* ── Reading view (wrapper) ───────────────────────────────────────────────
   Fiction / Nonfiction toggle only — no "All". Reading status (last read /
   currently reading / reading next) is a per-track slot, and the combined
   view lives on the Stats page. Default is Fiction (seeded from ?type=). */

export default function ReadingView({
  fiction,
  nonfiction,
  initialType = "fiction",
}: {
  fiction: { status: ReadingStatusResponse };
  nonfiction: { status: ReadingStatusResponse };
  initialType?: TypeScope;
}) {
  const [type, setType] = useState<TypeScope>(initialType === "nonfiction" ? "nonfiction" : "fiction");
  const isNon = type === "nonfiction";
  return (
    <div>
      <TypeToggle value={isNon ? "nonfiction" : "fiction"} onChange={setType} includeAll={false} />
      {isNon ? (
        <ReadingSingle status={nonfiction.status} kind="nonfiction" />
      ) : (
        <ReadingSingle status={fiction.status} kind="fiction" />
      )}
    </div>
  );
}
