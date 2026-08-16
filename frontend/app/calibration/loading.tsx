import { PageSkeleton, SkeletonStatCards, SkeletonPanel } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <PageSkeleton
        title="Model Calibration"
        titleClassName="font-display text-2xl font-semibold"
      >
        <SkeletonStatCards count={4} />
        <div className="mt-6 space-y-4">
          <SkeletonPanel lines={3} />
          <SkeletonPanel lines={5} />
        </div>
      </PageSkeleton>
    </div>
  );
}
