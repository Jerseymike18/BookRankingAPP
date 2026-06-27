"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useRef } from "react";

const sections = [
  {
    label: "Rankings",
    items: [
      { href: "/rankings", label: "Rankings" },
      { href: "/tier-list", label: "Tier List" },
      { href: "/series", label: "Series" },
    ],
  },
  {
    label: "Predictions",
    items: [
      { href: "/predict", label: "Predict" },
      { href: "/read-queue", label: "Read Queue" },
    ],
  },
  {
    label: "Library",
    items: [
      { href: "/reading", label: "Reading" },
      { href: "/add-book", label: "Add a Book" },
    ],
  },
  {
    label: "Miscellaneous",
    items: [
      { href: "/timeline", label: "Timeline" },
      { href: "/delta-log", label: "Delta Log" },
      { href: "/calibration", label: "Calibration" },
    ],
  },
];

function NavSection({
  section,
  currentPath,
}: {
  section: (typeof sections)[number];
  currentPath: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const isActive = section.items.some((item) => currentPath.startsWith(item.href));

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        className="px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1"
        style={{
          color: isActive ? "var(--color-sage)" : "var(--color-muted)",
          background: isActive ? "var(--color-sage-light)" : "transparent",
        }}
      >
        {section.label}
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          aria-hidden="true"
          style={{
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 150ms",
          }}
        >
          <path
            d="M2 4l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div
          className="absolute top-full left-0 mt-1 rounded-md border py-1 z-50 min-w-max"
          style={{
            background: "var(--color-surface)",
            borderColor: "var(--color-rule)",
          }}
        >
          {section.items.map(({ href, label }) => {
            const active = currentPath.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                className="block px-4 py-1.5 text-sm font-medium no-underline transition-colors"
                style={{
                  color: active ? "var(--color-sage)" : "var(--color-ink)",
                  background: active ? "var(--color-sage-light)" : "transparent",
                }}
              >
                {label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function Nav() {
  const path = usePathname();

  return (
    <header
      className="sticky top-0 z-50 border-b"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-rule)",
      }}
    >
      <div className="max-w-5xl mx-auto px-4 flex items-center gap-8 h-14">
        {/* Wordmark */}
        <Link href="/" className="flex-shrink-0 no-underline">
          <span
            className="font-display text-xl font-semibold leading-none"
            style={{ color: "var(--color-ink)" }}
          >
            The Reading Ledger
          </span>
        </Link>

        {/* Nav sections */}
        <nav className="flex items-center gap-1 flex-wrap">
          {sections.map((section) => (
            <NavSection key={section.label} section={section} currentPath={path} />
          ))}
        </nav>
      </div>
    </header>
  );
}
