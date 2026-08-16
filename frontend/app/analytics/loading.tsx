import { PageSkeleton, Skeleton, SkeletonStatCards, SkeletonChart } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton
      title="Taste Lab"
      subtitle="What your ratings reveal about what you actually love."
    >
      {/* Genre filter chips */}
      <div aria-hidden className="flex flex-wrap gap-2 mb-6">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} w={`${4 + (i % 3)}rem`} h="1.6rem" radius="9999px" />
        ))}
      </div>
      <SkeletonStatCards count={4} />
      <div className="mt-8 space-y-4">
        <SkeletonChart h="12rem" />
        <SkeletonChart h="12rem" />
      </div>
    </PageSkeleton>
  );
}
