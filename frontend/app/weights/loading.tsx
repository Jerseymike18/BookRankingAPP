import { PageSkeleton, Skeleton, SkeletonToggle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Genre Weights">
      <SkeletonToggle tabs={2} />
      <Skeleton h="2.25rem" radius="0.5rem" className="mb-4" />
      <div aria-hidden className="space-y-2">
        {Array.from({ length: 8 }, (_, i) => (
          <div
            key={i}
            className="flex items-center gap-4 px-5 py-4 rounded-xl"
            style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
          >
            <Skeleton h="0.85rem" w="9rem" />
            <div className="flex-1" />
            <Skeleton h="0.7rem" w="10rem" />
          </div>
        ))}
      </div>
    </PageSkeleton>
  );
}
