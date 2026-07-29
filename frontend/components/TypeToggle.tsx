"use client";

import type { TypeScope } from "@/lib/scope";

// Re-export so the view components can keep importing the type from here
// alongside the component. The runtime parser lives in @/lib/scope (server-safe).
export type { TypeScope };

const TABS: { id: TypeScope; label: string }[] = [
  { id: "all", label: "All" },
  { id: "fiction", label: "Fiction" },
  { id: "nonfiction", label: "Nonfiction" },
];

/** Pill toggle for the fiction / nonfiction / all content-type split. Reuses
 * the existing SubTabs visual (surface-2 track, sage active pill) — no new
 * design tokens. `includeAll` drops the "All" option for views that can only
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
    <div
      className={`flex gap-1 p-1 rounded-xl inline-flex ${className}`}
      style={{ background: "var(--color-surface-2)" }}
      role="tablist"
      aria-label="Content type"
    >
      {tabs.map(({ id, label }) => (
        <button
          key={id}
          role="tab"
          aria-selected={value === id}
          onClick={() => onChange(id)}
          className="px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
          style={{
            background: value === id ? "var(--color-surface)" : "transparent",
            color: value === id ? "var(--color-sage)" : "var(--color-muted)",
            boxShadow: value === id ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
