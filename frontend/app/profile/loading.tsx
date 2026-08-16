import { PageSkeleton, Skeleton } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="max-w-lg">
      <PageSkeleton
        title="My Profile"
        subtitle="Claim a handle and choose whether other readers can browse your rankings and to-read queue."
      >
        <div aria-hidden className="space-y-5">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i}>
              <Skeleton h="0.6rem" w="6rem" className="mb-2" />
              <Skeleton h="2.4rem" radius="0.5rem" />
            </div>
          ))}
          <Skeleton h="2.6rem" w="8rem" radius="0.75rem" />
        </div>
      </PageSkeleton>
    </div>
  );
}
