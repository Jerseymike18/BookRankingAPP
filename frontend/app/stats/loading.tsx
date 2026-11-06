import { PageSkeleton, SkeletonStatCards, SkeletonToggle, SkeletonList, Skeleton } from "@/components/Skeleton";

// /stats fetches three payloads (fiction books, nonfiction books, combined
// stats) before it can render, so the shell shows the dashboard's shape —
// headline tiles, the Rankings list, then the summaries below it.
const card = { background: "var(--color-surface)", boxShadow: "var(--shadow-card)" } as const;

export default function Loading() {
  return (
    <PageSkeleton title="Stats">
      <SkeletonStatCards count={4} cols="grid-cols-2 lg:grid-cols-4" />

      <Skeleton h="1.1rem" w="7rem" className="mt-8 mb-3" />
      <SkeletonToggle tabs={3} />
      <SkeletonList rows={8} />
      <div className="grid lg:grid-cols-2 gap-x-6">
        <div>
          <Skeleton h="1.1rem" w="6rem" className="mt-8 mb-3" />
          <div className="rounded-2xl p-4 space-y-4" style={card}>
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="flex items-center gap-3">
                <Skeleton w="2.25rem" h="2.25rem" radius="9999px" />
                <div className="flex-1 space-y-2">
                  <Skeleton h="0.8rem" w={`${70 - i * 8}%`} />
                  <Skeleton h="0.6rem" w="35%" />
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <Skeleton h="1.1rem" w="4rem" className="mt-8 mb-3" />
          <div className="rounded-2xl p-4 space-y-6" style={card}>
            <Skeleton h="0.75rem" radius="9999px" />
            <Skeleton h="0.75rem" radius="9999px" />
          </div>
        </div>
      </div>

      <Skeleton h="1.1rem" w="8rem" className="mt-8 mb-3" />
      <div className="rounded-2xl p-4" style={card}>
        <Skeleton h="10rem" radius="0.5rem" />
      </div>

    </PageSkeleton>
  );
}
