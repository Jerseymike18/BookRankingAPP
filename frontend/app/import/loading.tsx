import { PageSkeleton, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton
      title="Import from Goodreads"
      subtitle="Upload your Goodreads library export so you only have to rank your books — not re-enter every one's metadata."
    >
      <SkeletonPanel lines={4} />
    </PageSkeleton>
  );
}
