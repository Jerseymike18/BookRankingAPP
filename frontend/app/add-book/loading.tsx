import { PageSkeleton, Skeleton, SkeletonToggle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Add a Book" subtitle="Scores are 0–10 across the component rubric.">
      <Skeleton h="3rem" radius="0.5rem" className="mb-6" />
      <SkeletonToggle tabs={2} />
      {/* Metadata fields, then the component score grid */}
      <div aria-hidden className="grid sm:grid-cols-2 gap-4 mb-8">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i}>
            <Skeleton h="0.6rem" w="5rem" className="mb-2" />
            <Skeleton h="2.4rem" radius="0.5rem" />
          </div>
        ))}
      </div>
      <div aria-hidden className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {Array.from({ length: 14 }, (_, i) => (
          <Skeleton key={i} h="4rem" radius="0.5rem" />
        ))}
      </div>
    </PageSkeleton>
  );
}
