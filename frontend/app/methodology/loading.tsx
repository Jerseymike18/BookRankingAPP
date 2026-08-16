import { PageSkeleton, SkeletonToggle, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="max-w-3xl mx-auto">
      <PageSkeleton
        title="How the Engine Works"
        titleClassName="font-display text-3xl font-semibold"
      >
        {/* Plain English / Technical pill toggle */}
        <SkeletonToggle tabs={2} />
        <div className="space-y-4">
          <SkeletonPanel lines={5} />
          <SkeletonPanel lines={4} />
          <SkeletonPanel lines={6} />
        </div>
      </PageSkeleton>
    </div>
  );
}
