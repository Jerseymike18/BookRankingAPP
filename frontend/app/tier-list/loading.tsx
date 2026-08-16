import { PageSkeleton, SkeletonTierLadder } from "@/components/Skeleton";

// The heaviest read on the site: one /api/tiers call per track plus one per
// year the reader has, so the ladder is worth a real placeholder.
export default function Loading() {
  return (
    <PageSkeleton title="Tier List" toggle="above">
      <SkeletonTierLadder />
    </PageSkeleton>
  );
}
