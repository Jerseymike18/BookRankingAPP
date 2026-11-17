import { PageSkeleton, SkeletonToggle, SkeletonList, Skeleton } from "@/components/Skeleton";

// /stats fetches its payloads (fiction + nonfiction books, combined stats,
// tiers) before it can render, so the shell shows the page's shape — the
// Rankings list, then the books-per-year chart below it.
const card = { background: "var(--color-surface)", boxShadow: "var(--shadow-card)" } as const;

export default function Loading() {
  return (
    <PageSkeleton title="Stats">
      <Skeleton h="1.1rem" w="7rem" className="mb-3" />
      <SkeletonToggle tabs={3} />
      <SkeletonList rows={8} />

      <Skeleton h="1.1rem" w="8rem" className="mt-8 mb-3" />
      <div className="rounded-2xl p-4" style={card}>
        <Skeleton h="10rem" radius="0.5rem" />
      </div>

    </PageSkeleton>
  );
}
