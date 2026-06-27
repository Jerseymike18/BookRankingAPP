"use client";

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
}

function TierRow({ tier, items }: { tier: string; items: TierItem[] }) {
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
            title={item.label}
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
  return (
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
          <TierRow tier={tier} items={itemsByTier[tier] ?? []} />
        </div>
      ))}
    </div>
  );
}
