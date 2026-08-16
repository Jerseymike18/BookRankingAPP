import { PageSkeleton, Skeleton, SkeletonTable, SkeletonChart } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Timeline" toggle="above">
      <Skeleton h="1.2rem" w="6rem" className="mb-4" />
      <SkeletonTable rows={6} cols={6} />
      <div className="mt-6 space-y-4">
        <SkeletonChart h="9rem" />
        <SkeletonChart h="9rem" />
      </div>
    </PageSkeleton>
  );
}
