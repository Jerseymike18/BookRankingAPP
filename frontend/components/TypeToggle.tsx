"use client";

import type { TypeScope } from "@/lib/scope";
import { SegmentedTabs } from "@/components/SegmentedTabs";

// Re-export so the view components can keep importing the type from here
// alongside the component. The runtime parser lives in @/lib/scope (server-safe).
export type { TypeScope };

const TABS: { id: TypeScope; label: string }[] = [
  { id: "all", label: "All" },
  { id: "fiction", label: "Fiction" },
  { id: "nonfiction", label: "Nonfiction" },
];

/** Pill toggle for the fiction / nonfiction / all content-type split. Renders as
 * the shared SegmentedTabs underline control. `includeAll` drops the "All" option for views that can only
 * show one type at a time. */
export function TypeToggle({
  value,
  onChange,
  includeAll = true,
  className = "mb-6",
}: {
  value: TypeScope;
  onChange: (t: TypeScope) => void;
  includeAll?: boolean;
  className?: string;
}) {
  const tabs = includeAll ? TABS : TABS.filter((t) => t.id !== "all");
  return (
    <SegmentedTabs tabs={tabs} value={value} onChange={onChange} className={className} ariaLabel="Content type" />
  );
}
