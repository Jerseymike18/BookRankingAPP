import { PageSkeleton, SkeletonStatCards, SkeletonTable } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <PageSkeleton
        title="Track Record"
        titleClassName="font-display text-2xl font-semibold"
      >
        <SkeletonStatCards count={4} />
        <div className="mt-6">
          <SkeletonTable rows={8} cols={6} />
        </div>
      </PageSkeleton>
    </div>
  );
}
