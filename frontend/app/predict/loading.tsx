import { PageSkeleton, Skeleton, SkeletonToggle } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton
      title="Predict"
      subtitle="Ask the LLM to discover candidates — or name a single book — then let your engine score and rank them."
    >
      <SkeletonToggle tabs={2} />
      {/* The request card */}
      <div
        className="rounded-xl p-5"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <Skeleton h="0.9rem" w="14rem" className="mb-3" />
        <Skeleton h="0.7rem" w="70%" className="mb-4" />
        <Skeleton h="4rem" radius="0.5rem" />
        <Skeleton h="2.5rem" w="12rem" radius="0.75rem" className="mt-4" />
      </div>
    </PageSkeleton>
  );
}
