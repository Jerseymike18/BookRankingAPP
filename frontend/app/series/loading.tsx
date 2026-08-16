import { PageSkeleton, SkeletonToggle, SkeletonTable } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Series" toggle="above">
      <SkeletonToggle tabs={2} />
      <SkeletonTable rows={8} cols={6} />
    </PageSkeleton>
  );
}
