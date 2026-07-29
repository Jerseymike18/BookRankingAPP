"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import { READONLY } from "@/lib/readonly";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";

/** The Sign-out affordance renders only when Supabase is configured — i.e. the
 * hosted multi-tenant build. Local dev + the static public build leave the env
 * unset, so the nav looks and behaves exactly as before. */
const AUTH_CONFIGURED =
  !!process.env.NEXT_PUBLIC_SUPABASE_URL &&
  !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

async function signOut() {
  try {
    await createSupabaseBrowserClient().auth.signOut();
  } catch {
    /* ignore — navigate to /login regardless */
  }
  window.location.assign("/login");
}

type NavItem = { href: string; label: string };
type NavGroup =
  | { label: string; items: NavItem[] }            // dropdown section
  | { label: string; href: string };               // top-level direct link

// Explore — read-only-safe analytical views. Each of Tier List / Series /
// Timeline carries its own in-page Fiction / Nonfiction toggle; Stats renders
// combined + per-type, so it needs none.
const EXPLORE_ITEMS: NavItem[] = [
  { href: "/tier-list", label: "Tier List" },
  { href: "/series", label: "Series" },
  { href: "/timeline", label: "Timeline" },
  { href: "/stats", label: "Stats" },
];

// Reading — the currently-reading view, the single de-duplicated Read Queue, and
// (write-only) Add a Book. Currently Reading + Read Queue render view-only, so
// they stay on the read-only build; Add a Book is dropped there (self-guards too).
const READING_ITEMS: NavItem[] = [
  { href: "/reading", label: "Currently Reading" },
  { href: "/read-queue", label: "Read Queue" },
  ...(READONLY ? [] : [{ href: "/add-book", label: "Add a Book" }]),
];

// "For Nerds" — how-it-works + deep diagnostics. Methodology / Track Record /
// Calibration / Taste Lab all render read-only, so they stay on the public
// showcase; the per-user / first-run features (Genre Weights, Tutorial) are
// dropped there (each page self-guards with ComingSoon). Tutorial keeps a home
// here now that it no longer has its own permanent nav slot. Delta Log is
// intentionally gone from the nav entirely (the DeltaTracker layer is retired).
const NERD_ITEMS: NavItem[] = [
  { href: "/methodology", label: "Methodology" },
  { href: "/track-record", label: "Track Record" },
  { href: "/calibration", label: "Calibration" },
  { href: "/analytics", label: "Taste Lab" },
  ...(READONLY
    ? []
    : [
        { href: "/weights", label: "Genre Weights" },
        { href: "/welcome", label: "Tutorial" },
      ]),
];

const sections: NavGroup[] = [
  // Rankings is the primary destination — a direct link; the Fiction /
  // Nonfiction / All split lives in an in-page toggle on the page itself.
  { label: "Rankings", href: "/rankings" },
  { label: "Explore", items: EXPLORE_ITEMS },
  { label: "Reading", items: READING_ITEMS },
  // Predict is the primary write/compute action — a top-level link, dropped on
  // the read-only build (the page also self-guards).
  ...(READONLY ? [] : [{ label: "Predict", href: "/predict" }]),
  { label: "For Nerds", items: NERD_ITEMS },
];

function isDropdown(s: NavGroup): s is { label: string; items: NavItem[] } {
  return "items" in s;
}

function NavSection({
  section,
  currentPath,
}: {
  section: { label: string; items: NavItem[] };
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

function NavLink({ href, label, currentPath }: NavItem & { currentPath: string }) {
  const isActive = currentPath === href || currentPath.startsWith(href + "/");
  return (
    <Link
      href={href}
      className="px-3 py-1.5 rounded-md text-sm font-medium no-underline transition-colors"
      style={{
        color: isActive ? "var(--color-sage)" : "var(--color-muted)",
        background: isActive ? "var(--color-sage-light)" : "transparent",
      }}
    >
      {label}
    </Link>
  );
}

// Single link inside the mobile menu panel. `standalone` items (e.g. Stats)
// align with the section headers; grouped items are indented under them.
function MobileNavLink({
  href,
  label,
  currentPath,
  onNavigate,
  standalone,
}: NavItem & {
  currentPath: string;
  onNavigate: () => void;
  standalone?: boolean;
}) {
  const isActive = currentPath === href || currentPath.startsWith(href + "/");
  return (
    <Link
      href={href}
      onClick={onNavigate}
      className={`block rounded-md py-3 text-sm font-medium no-underline transition-colors ${
        standalone ? "px-2" : "px-4"
      }`}
      style={{
        color: isActive ? "var(--color-sage)" : "var(--color-ink)",
        background: isActive ? "var(--color-sage-light)" : "transparent",
      }}
    >
      {label}
    </Link>
  );
}

export default function Nav() {
  const path = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close the mobile menu on Escape.
  useEffect(() => {
    if (!mobileOpen) return;
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") setMobileOpen(false);
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [mobileOpen]);

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
        <Link
          href="/"
          onClick={() => setMobileOpen(false)}
          className="flex-shrink-0 no-underline"
        >
          <span
            className="font-display text-xl font-semibold leading-none"
            style={{ color: "var(--color-ink)" }}
          >
            The Reading Ledger
          </span>
        </Link>

        {/* Desktop nav sections — collapse to a menu on small screens */}
        <nav className="hidden md:flex items-center gap-1 flex-wrap">
          {sections.map((section) =>
            isDropdown(section) ? (
              <NavSection key={section.label} section={section} currentPath={path} />
            ) : (
              <NavLink
                key={section.label}
                href={section.href}
                label={section.label}
                currentPath={path}
              />
            )
          )}
        </nav>

        {/* Sign out (hosted multi-tenant build only) */}
        {AUTH_CONFIGURED && (
          <button
            onClick={signOut}
            className="hidden md:inline-flex ml-auto px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
            style={{ color: "var(--color-muted)" }}
          >
            Sign out
          </button>
        )}

        {/* Mobile menu toggle */}
        <button
          onClick={() => setMobileOpen((o) => !o)}
          aria-label={mobileOpen ? "Close menu" : "Open menu"}
          aria-expanded={mobileOpen}
          className="md:hidden ml-auto -mr-2 flex items-center justify-center rounded-md p-2"
          style={{ color: "var(--color-ink)" }}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d={mobileOpen ? "M6 6l12 12M18 6L6 18" : "M4 7h16M4 12h16M4 17h16"}
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>

      {/* Mobile menu panel */}
      {mobileOpen && (
        <nav
          className="md:hidden border-t max-h-[calc(100vh-3.5rem)] overflow-y-auto"
          style={{
            background: "var(--color-surface)",
            borderColor: "var(--color-rule)",
          }}
        >
          <div className="max-w-5xl mx-auto px-4 py-2 flex flex-col">
            {sections.map((section) =>
              isDropdown(section) ? (
                <div key={section.label} className="py-1">
                  <div
                    className="px-2 pt-2 pb-1 text-xs font-semibold uppercase tracking-wider"
                    style={{ color: "var(--color-faint)" }}
                  >
                    {section.label}
                  </div>
                  {section.items.map((item) => (
                    <MobileNavLink
                      key={item.href}
                      href={item.href}
                      label={item.label}
                      currentPath={path}
                      onNavigate={() => setMobileOpen(false)}
                    />
                  ))}
                </div>
              ) : (
                <MobileNavLink
                  key={section.label}
                  href={section.href}
                  label={section.label}
                  currentPath={path}
                  onNavigate={() => setMobileOpen(false)}
                  standalone
                />
              )
            )}
            {AUTH_CONFIGURED && (
              <button
                onClick={() => {
                  setMobileOpen(false);
                  signOut();
                }}
                className="mt-1 text-left rounded-md py-3 px-2 text-sm font-medium transition-colors"
                style={{ color: "var(--color-muted)" }}
              >
                Sign out
              </button>
            )}
          </div>
        </nav>
      )}
    </header>
  );
}
