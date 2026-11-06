import { PageSkeleton, SkeletonToggle, SkeletonList } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Series" toggle="above">
      <SkeletonToggle tabs={2} />
      <SkeletonList rows={8} badge={false} />
    </PageSkeleton>
  );
}
