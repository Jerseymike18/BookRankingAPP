import { PageSkeleton, SkeletonStatCards, SkeletonTable } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <PageSkeleton
        title="Series Breakdown"
        titleClassName="font-display text-3xl font-bold"
      >
        <SkeletonStatCards count={4} />
        <div className="mt-6">
          <SkeletonTable rows={10} cols={9} />
        </div>
      </PageSkeleton>
    </div>
  );
}
