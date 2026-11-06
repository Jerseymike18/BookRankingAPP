"use client";

import { useState } from "react";
import WeightsClient from "./WeightsClient";
import type { EffectiveWeights, BookKind } from "@/lib/types";
import { SegmentedTabs, KIND_TABS } from "@/components/SegmentedTabs";

/** Header + Fiction/Nonfiction toggle around the per-track editor. The editor is
 *  remounted (`key={kind}`) on toggle so its slider state re-seeds cleanly from
 *  the selected track's payload. */
export default function WeightsPageClient({
  fiction,
  nonfiction,
}: {
  fiction: EffectiveWeights;
  nonfiction: EffectiveWeights;
}) {
  const [kind, setKind] = useState<BookKind>("fiction");
  const payloads: Record<BookKind, EffectiveWeights> = { fiction, nonfiction };

  return (
    <div>
      <div className="mb-5">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Genre Weights
        </h1>
        <p className="mt-1 text-sm max-w-2xl" style={{ color: "var(--color-muted)" }}>
          Tailor how much each category (and each component within it) counts
          toward a genre’s weighted score. Sliders are relative — saved values are
          normalized to 100%. Saving re-ranks your library immediately.
        </p>
      </div>

      {/* Fiction / Nonfiction toggle */}
      <SegmentedTabs tabs={KIND_TABS} value={kind} onChange={setKind} className="mb-6" />

      <WeightsClient key={kind} initial={payloads[kind]} kind={kind} />
    </div>
  );
}
