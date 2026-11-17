import { PageSkeleton, SkeletonStatCards, Skeleton } from "@/components/Skeleton";

// /profile leads with the reader's summary (headline tiles + tiers), then the
// handle settings form.
const card = { background: "var(--color-surface)", boxShadow: "var(--shadow-card)" } as const;

export default function Loading() {
  return (
    <PageSkeleton title="My Profile">
      <SkeletonStatCards count={4} cols="grid-cols-2 lg:grid-cols-4" />

      <Skeleton h="1.1rem" w="4rem" className="mt-8 mb-3" />
      <div className="rounded-2xl p-4 space-y-6" style={card}>
        <Skeleton h="0.75rem" radius="9999px" />
        <Skeleton h="0.75rem" radius="9999px" />
      </div>

      <div className="max-w-lg">
        <Skeleton h="1.1rem" w="7rem" className="mt-8 mb-4" />
        <div aria-hidden className="space-y-5">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i}>
              <Skeleton h="0.6rem" w="6rem" className="mb-2" />
              <Skeleton h="2.4rem" radius="0.5rem" />
            </div>
          ))}
          <Skeleton h="2.6rem" w="8rem" radius="0.75rem" />
        </div>
      </div>
    </PageSkeleton>
  );
}
