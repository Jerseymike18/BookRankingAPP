import { Skeleton, SkeletonPanel } from "@/components/Skeleton";

// The wizard's own step bar is data-independent, so the shell keeps the hero
// and shows the four step segments unfilled while the weights + anchors load.
export default function Loading() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading Welcome">
      <span className="sr-only">Loading the setup wizard…</span>
      <div className="mb-5">
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
        <p className="mt-2 text-sm max-w-2xl leading-relaxed" style={{ color: "var(--color-muted)" }}>
          A quick tour, then four short setup steps — about a minute. You can change
          everything later.
        </p>
      </div>

      <div aria-hidden className="mb-5">
        <div className="flex gap-1.5 mb-2">
          {Array.from({ length: 4 }, (_, i) => (
            <div
              key={i}
              className="h-1.5 flex-1 rounded-full"
              style={{ background: "var(--color-rule)" }}
            />
          ))}
        </div>
        <Skeleton h="0.6rem" w="14rem" />
      </div>

      <div className="space-y-4">
        <SkeletonPanel lines={5} />
        <SkeletonPanel lines={3} />
      </div>
    </div>
  );
}
