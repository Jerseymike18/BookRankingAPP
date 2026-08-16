import { PageSkeleton, SkeletonToggle, SkeletonCardList } from "@/components/Skeleton";

// Four fetches gate this page (fiction + nonfiction read-queue and ordered
// queue), and the to-read list is usually the longest payload in the app.
export default function Loading() {
  return (
    <PageSkeleton title="Read Queue" toggle="below" toggleTabs={2}>
      <SkeletonToggle tabs={2} />
      <SkeletonCardList cards={6} />
    </PageSkeleton>
  );
}
