"use client";

import { useEffect, useRef } from "react";

/**
 * SEGMENTED TABS — the one in-page tab control (Fiction / Nonfiction, Stats /
 * Status, year tabs, Plain English / Technical, …).
 *
 * App-style rather than web-button-style: a row of text tabs on a hairline rule,
 * the active one in sage with an underline — the same look as the phone shell's
 * section tabs (components/Nav.tsx). On phones a short set stretches to full
 * width; a long set (year tabs) scrolls sideways and keeps the active tab in
 * view. Fable tokens only.
 */
export function SegmentedTabs<T extends string>({
  tabs,
  value,
  onChange,
  className = "mb-6",
  ariaLabel,
}: {
  tabs: readonly { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const activeRef = useRef<HTMLButtonElement>(null);
  const scrolls = tabs.length > 4;

  useEffect(() => {
    if (scrolls) activeRef.current?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [scrolls, value]);

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`flex border-b ${scrolls ? "overflow-x-auto" : ""} ${className}`}
      style={{ borderColor: "var(--color-rule)", scrollbarWidth: "none" }}
    >
      {tabs.map(({ id, label }) => {
        const active = id === value;
        return (
          <button
            key={id}
            ref={active ? activeRef : undefined}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(id)}
            className={`relative h-10 px-4 text-sm font-medium whitespace-nowrap transition-colors ${
              scrolls ? "flex-none" : "flex-1 sm:flex-none"
            }`}
            style={{ color: active ? "var(--color-sage)" : "var(--color-muted)" }}
          >
            {label}
            <span
              aria-hidden="true"
              className="absolute -bottom-px left-3 right-3 h-0.5 rounded-full transition-opacity"
              style={{ background: "var(--color-sage)", opacity: active ? 1 : 0 }}
            />
          </button>
        );
      })}
    </div>
  );
}

export const KIND_TABS = [
  { id: "fiction", label: "Fiction" },
  { id: "nonfiction", label: "Nonfiction" },
] as const;
