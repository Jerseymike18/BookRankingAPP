import { PageSkeleton, SkeletonToggle, SkeletonTable } from "@/components/Skeleton";

// A public profile fans out to the target reader's books, tiers, stats and
// read-queue (both tracks) before it can render — the widest fetch on the site.
// The handle isn't known to a Server Component here, so the heading is a
// placeholder too.
export default function Loading() {
  return (
    <PageSkeleton>
      <SkeletonToggle tabs={4} />
      <SkeletonTable rows={10} cols={6} />
    </PageSkeleton>
  );
}
