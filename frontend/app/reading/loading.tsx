import { PageSkeleton, SkeletonToggle, SkeletonStatCards, SkeletonChart } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Reading" toggle="above">
      <SkeletonToggle tabs={2} />
      <SkeletonStatCards count={4} />
      <div className="mt-6 space-y-4">
        <SkeletonChart h="10rem" />
        <SkeletonChart h="10rem" />
      </div>
    </PageSkeleton>
  );
}
