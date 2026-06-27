import { Suspense } from "react";
import DeltaLogClient from "./DeltaLogClient";

export const metadata = { title: "Delta Log — The Reading Ledger" };

export default function DeltaLogPage() {
  return (
    <Suspense fallback={<p style={{ color: "var(--color-muted)" }}>Loading…</p>}>
      <DeltaLogClient />
    </Suspense>
  );
}
