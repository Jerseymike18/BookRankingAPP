"use client";

import { useState, useCallback } from "react";

const TIER_COLORS: Record<string, string> = {
  "S+": "#2D6A4F",
  "S":  "#4A7C59",
  "A":  "#7BA87B",
  "B":  "#D4A853",
  "C":  "#C07C5A",
  "D":  "#7B8FA1",
  "F":  "#C4B8AD",
};

export interface TierItem {
  label: string;
  author?: string;
  genre?: string;
  series?: string;
  seriesNumber?: number | null;
  words?: number | null;
  yearRead?: number | null;
  rank?: number;
  score?: number;
  scoreLabel?: string;
  tier?: string;
}

/* ── Hover tooltip ────────────────────────────────────────────────────────────
   Reuses the analytics dark-ink tooltip look. Rendered as a fixed-position
   sibling of the ladder (not a child) so the ladder's overflow:hidden — which
   keeps the rounded corners — never clips it. Positioned off each box's rect on
   mouse-enter, flipping above when the box sits near the bottom of the viewport. */

interface HoverState {
  item: TierItem;
  left: number;
  top: number;
  flipUp: boolean;
}

function TierTip({ hover }: { hover: HoverState }) {
  const { item } = hover;
  const scoreLine = [
    item.tier,
    item.rank != null ? `#${item.rank}` : null,
    item.score != null ? `${item.scoreLabel ?? "Score"} ${item.score.toFixed(2)}` : null,
  ]
    .filter(Boolean)
    .join("  ·  ");
  const genreSeries = [
    item.genre,
    item.series
      ? `${item.series}${item.seriesNumber != null ? ` #${item.seriesNumber}` : ""}`
      : null,
  ]
    .filter(Boolean)
    .join("  ·  ");
  const wordsYear = [
    item.words != null ? `${item.words.toLocaleString()} words` : null,
    item.yearRead != null ? `read ${item.yearRead}` : null,
  ]
    .filter(Boolean)
    .join("  ·  ");

  return (
    <div
      style={{
        position: "fixed",
        left: hover.left,
        top: hover.top,
        transform: hover.flipUp ? "translateY(-100%)" : "none",
        pointerEvents: "none",
        zIndex: 50,
        maxWidth: 260,
        background: "var(--color-ink)",
        color: "#fff",
        padding: "8px 10px",
        borderRadius: 6,
        fontSize: 11.5,
        lineHeight: 1.4,
        boxShadow: "0 4px 14px rgba(0,0,0,0.28)",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: 700,
          fontSize: 12.5,
          marginBottom: 2,
        }}
      >
        {item.label}
      </div>
      {item.author && (
        <div style={{ color: "rgba(255,255,255,0.72)", marginBottom: 3 }}>
          by {item.author}
        </div>
      )}
      {scoreLine && <div style={{ fontVariantNumeric: "tabular-nums" }}>{scoreLine}</div>}
      {genreSeries && <div style={{ color: "rgba(255,255,255,0.7)" }}>{genreSeries}</div>}
      {wordsYear && (
        <div style={{ color: "rgba(255,255,255,0.7)", fontVariantNumeric: "tabular-nums" }}>
          {wordsYear}
        </div>
      )}
    </div>
  );
}

function TierRow({
  tier,
  items,
  onHover,
  onLeave,
}: {
  tier: string;
  items: TierItem[];
  onHover: (item: TierItem, e: React.MouseEvent<HTMLDivElement>) => void;
  onLeave: () => void;
}) {
  const color = TIER_COLORS[tier] ?? "#8a7d6b";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "stretch",
        borderBottom: "1px solid var(--color-rule)",
      }}
    >
      {/* Label block */}
      <div
        style={{
          width: 72,
          flexShrink: 0,
          background: color,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 64,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 700,
            fontSize: "1.25rem",
            color: "#fff",
            textShadow: "0 1px 2px rgba(0,0,0,0.25)",
          }}
        >
          {tier}
        </span>
      </div>

      {/* Book/series boxes */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          padding: "8px 10px",
          background: "var(--color-surface)",
          alignContent: "flex-start",
          minHeight: 64,
        }}
      >
        {items.map((item, i) => (
          <div
            key={i}
            onMouseEnter={(e) => onHover(item, e)}
            onMouseLeave={onLeave}
            style={{
              height: 50,
              width: 144,
              padding: "0 10px",
              background: "var(--color-surface-2)",
              border: "1px solid var(--color-rule)",
              borderRadius: 6,
              display: "flex",
              alignItems: "center",
              overflow: "hidden",
              cursor: "default",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: "0.78rem",
                color: "var(--color-ink)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                lineHeight: 1.3,
              }}
            >
              {item.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function TierLadder({
  tierOrder,
  itemsByTier,
}: {
  tierOrder: string[];
  itemsByTier: Record<string, TierItem[]>;
}) {
  const [hover, setHover] = useState<HoverState | null>(null);

  const onHover = useCallback((item: TierItem, e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const flipUp = r.bottom > window.innerHeight - 150;
    setHover({
      item,
      left: Math.max(8, Math.min(r.left, window.innerWidth - 268)),
      top: flipUp ? r.top - 6 : r.bottom + 6,
      flipUp,
    });
  }, []);

  const onLeave = useCallback(() => setHover(null), []);

  return (
    <>
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: 12,
          overflow: "hidden",
        }}
      >
        {tierOrder.map((tier, i) => (
          <div
            key={tier}
            style={i === tierOrder.length - 1 ? { borderBottom: "none" } : undefined}
          >
            <TierRow
              tier={tier}
              items={itemsByTier[tier] ?? []}
              onHover={onHover}
              onLeave={onLeave}
            />
          </div>
        ))}
      </div>
      {hover && <TierTip hover={hover} />}
    </>
  );
}
