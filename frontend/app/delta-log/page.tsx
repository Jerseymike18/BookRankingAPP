import { Suspense } from "react";
import DeltaLogClient from "./DeltaLogClient";
import { PageSkeleton, SkeletonTable } from "@/components/Skeleton";

export const metadata = { title: "Delta Log — The Reading Ledger" };

// Same shell the client renders while its own fetch is in flight, so the
// Suspense boundary and the fetch wait look like one continuous load.
function DeltaLogSkeleton() {
  return (
    <PageSkeleton title="Delta Log" titleClassName="font-display text-2xl font-semibold">
      <SkeletonTable rows={8} cols={6} />
    </PageSkeleton>
  );
}

export default function DeltaLogPage() {
  return (
    <Suspense fallback={<DeltaLogSkeleton />}>
      <DeltaLogClient />
    </Suspense>
  );
}
