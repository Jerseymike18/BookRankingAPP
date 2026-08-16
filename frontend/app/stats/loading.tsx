import { PageSkeleton, SkeletonStatCards, SkeletonTable, Skeleton } from "@/components/Skeleton";

// /stats fetches three payloads (fiction books, nonfiction books, combined
// stats) before it can render, so the shell shows the dashboard's shape —
// totals band, per-type cards, tier distribution — then the Rankings tables.
export default function Loading() {
  return (
    <PageSkeleton title="Stats">
      <SkeletonStatCards count={5} cols="grid-cols-2 sm:grid-cols-5" />

      <Skeleton h="1.1rem" w="7rem" className="mt-10 mb-3" />
      <SkeletonStatCards count={2} cols="sm:grid-cols-2" />

      <Skeleton h="1.1rem" w="10rem" className="mt-10 mb-3" />
      <div
        className="rounded-xl p-4 space-y-3"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <Skeleton h="1.5rem" />
        <Skeleton h="1.5rem" />
      </div>

      <Skeleton h="1.1rem" w="8rem" className="mt-10 mb-3" />
      <SkeletonTable rows={10} cols={6} />
    </PageSkeleton>
  );
}
