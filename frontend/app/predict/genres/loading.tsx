import { PageSkeleton, Skeleton } from "@/components/Skeleton";

export default function Loading() {
  return (
    <PageSkeleton
      title="Genre Prediction"
      subtitle="Which genres your own ratings actually favour — and where the engine has been wrong about you."
    >
      {/* The ask card. No toggle skeleton: this page has no fiction/nonfiction
          switch — genre evidence is fiction-only. */}
      <div
        className="rounded-xl p-5"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <Skeleton h="0.9rem" w="16rem" className="mb-3" />
        <Skeleton h="0.7rem" w="80%" className="mb-4" />
        <Skeleton h="2.5rem" radius="0.5rem" />
        <Skeleton h="2.5rem" w="12rem" radius="0.75rem" className="mt-4" />
      </div>
    </PageSkeleton>
  );
}
