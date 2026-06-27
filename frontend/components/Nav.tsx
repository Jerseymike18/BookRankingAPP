"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/reading", label: "Reading" },
  { href: "/read-queue", label: "Read Queue" },
  { href: "/predict", label: "Predict" },
  { href: "/rankings", label: "Rankings" },
  { href: "/tier-list", label: "Tier List" },
  { href: "/series", label: "Series" },
  { href: "/timeline", label: "Timeline" },
  { href: "/add-book", label: "Add a Book" },
];

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

        {/* Nav links */}
        <nav className="flex items-center gap-1">
          {links.map(({ href, label }) => {
            const active = path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className="px-3 py-1.5 rounded-md text-sm font-medium no-underline transition-colors"
                style={{
                  color: active ? "var(--color-sage)" : "var(--color-muted)",
                  background: active ? "var(--color-sage-light)" : "transparent",
                }}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
