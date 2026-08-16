import { PageSkeleton, SkeletonCardList } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton title="Directory">
      <SkeletonCardList cards={4} />
    </PageSkeleton>
  );
}
