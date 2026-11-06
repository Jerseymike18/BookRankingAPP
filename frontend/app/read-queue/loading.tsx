import { PageSkeleton, SkeletonToggle, SkeletonList } from "@/components/Skeleton";

// Four fetches gate this page (fiction + nonfiction read-queue and ordered
// queue), and the to-read list is usually the longest payload in the app.
// Shape matches the page: type toggle, heading, Mood/Queue tabs, the list.
export default function Loading() {
  return (
    <PageSkeleton title="Read Queue" toggle="above">
      <SkeletonToggle tabs={2} />
      <SkeletonList rows={8} />
    </PageSkeleton>
  );
}
