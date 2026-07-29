"use client";

import { useState } from "react";
import type { ReadQueueResponse, NonfictionReadQueueResponse } from "@/lib/types";
import { TypeToggle, type TypeScope } from "@/components/TypeToggle";
import ReadQueueClient from "./ReadQueueClient";
import NonfictionReadQueueClient from "./NonfictionReadQueueClient";

/** The single Read Queue route. Fiction and nonfiction have genuinely separate
 * queue clients (different recommendation shapes), so this mounts one or the
 * other behind a Fiction / Nonfiction toggle — no "All" (a queue is per-track).
 * Replaces the old duplicate `/nonfiction/read-queue` entry point. */
export default function ReadQueueTypeSwitch({
  fiction,
  nonfiction,
  initialType = "fiction",
}: {
  fiction: { data: ReadQueueResponse; initialQueue: string[] };
  nonfiction: { data: NonfictionReadQueueResponse; initialQueue: string[] };
  initialType?: TypeScope;
}) {
  const [type, setType] = useState<TypeScope>(
    initialType === "nonfiction" ? "nonfiction" : "fiction",
  );
  const isNon = type === "nonfiction";
  return (
    <div>
      <TypeToggle value={isNon ? "nonfiction" : "fiction"} onChange={setType} includeAll={false} />
      {isNon ? (
        <NonfictionReadQueueClient data={nonfiction.data} initialQueue={nonfiction.initialQueue} />
      ) : (
        <ReadQueueClient data={fiction.data} initialQueue={fiction.initialQueue} />
      )}
    </div>
  );
}
