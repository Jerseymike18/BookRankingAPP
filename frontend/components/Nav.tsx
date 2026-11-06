"use client";

/**
 * APP SHELL CHROME — the navigation that makes the Ledger read as an app rather
 * than a website. Three pieces, all fed by `nav-config.ts`:
 *
 *   <Sidebar />      desktop (md+): persistent left rail, every group expanded.
 *   <MobileTopBar /> phone (<md): compact title bar (+ Back on detail routes) and
 *                    a sub-tab strip for the active group's sibling pages.
 *   <TabBar />       phone (<md): fixed bottom tab bar, one tab per primary group
 *                    plus "More", which opens a bottom sheet with the rest.
 *
 * No new visual language: surfaces, rules, sage active states and the
 * surface-2 pill track are the existing Fable tokens / SubTabs pattern.
 * Every piece renders nothing on chromeless routes (/login).
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { READONLY } from "@/lib/readonly";
import { PredictJobPill } from "@/components/PredictJobStatus";
import { clearPredictJobs } from "@/lib/predict-jobs";
import { clearAllDrafts } from "@/lib/draft-storage";
import {
  NAV_GROUPS,
  activeGroup,
  activeItemHref,
  isChromeless,
  isDetailRoute,
  type IconName,
  type NavGroup,
} from "@/components/nav-config";

// Re-exported so existing imports (tests/nav-active.test.ts) keep working.
export { activeItemHref } from "@/components/nav-config";

/** The Sign-out affordance renders only when Supabase is configured — i.e. the
 * hosted multi-tenant build. Local dev + the static public build leave the env
 * unset. */
const AUTH_CONFIGURED =
  !!process.env.NEXT_PUBLIC_SUPABASE_URL &&
  !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

async function signOut() {
  // Drop the tab's stored prediction run and every page draft first: all of it
  // is sessionStorage, so it would otherwise outlive the sign-out and greet the
  // next person to use this tab with the previous reader's predictions, their
  // onboarding answers, or the book they were half-way through rating.
  clearPredictJobs();
  clearAllDrafts();
  try {
    await createSupabaseBrowserClient().auth.signOut();
  } catch {
    /* ignore — navigate to /login regardless */
  }
  window.location.assign("/login");
}

const TAB_GROUPS = NAV_GROUPS.filter((g) => g.tab);
const MORE_GROUPS = NAV_GROUPS.filter((g) => !g.tab);

/* ───────────────────────────── icons ───────────────────────────── */

const ICON_PATHS: Record<IconName, string> = {
  stats: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  reading: "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5",
  predict: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  explore: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM16.2 7.8l-2.1 6.3-6.3 2.1 2.1-6.3z",
  community: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  nerds: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v4M12 16h.01",
  more: "M5 12h.01M12 12h.01M19 12h.01",
};

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d={ICON_PATHS[name]}
        stroke="currentColor"
        strokeWidth={name === "more" ? 3 : 1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ───────────────────────────── shared bits ───────────────────────────── */

function Wordmark() {
  return (
    <Link href="/" className="no-underline">
      <span
        className="font-display text-xl font-semibold leading-none"
        style={{ color: "var(--color-ink)" }}
      >
        The Reading Ledger
      </span>
    </Link>
  );
}

/** A full-width list link — sidebar rows and More-sheet rows. */
function RowLink({
  href,
  label,
  active,
  icon,
  onNavigate,
}: {
  href: string;
  label: string;
  active: boolean;
  icon?: IconName;
  onNavigate?: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium no-underline transition-colors"
      style={{
        color: active ? "var(--color-sage)" : "var(--color-ink)",
        background: active ? "var(--color-sage-light)" : "transparent",
      }}
    >
      {icon && <Icon name={icon} size={18} />}
      {label}
    </Link>
  );
}

function GroupHeader({ group }: { group: NavGroup }) {
  return (
    <div
      className="flex items-center gap-2 px-3 pt-4 pb-1 text-xs font-semibold uppercase tracking-wider"
      style={{ color: "var(--color-faint)" }}
    >
      <Icon name={group.icon} size={14} />
      {group.label}
    </div>
  );
}

/** One group as a list: single-item groups are a plain row with the group
 *  icon; multi-item groups get a small header over their pages. */
function GroupList({
  group,
  path,
  onNavigate,
}: {
  group: NavGroup;
  path: string;
  onNavigate?: () => void;
}) {
  const activeHref = activeItemHref(group.items, path);
  if (group.items.length === 1) {
    const item = group.items[0];
    return (
      <RowLink
        href={item.href}
        label={group.label}
        icon={group.icon}
        active={activeHref === item.href}
        onNavigate={onNavigate}
      />
    );
  }
  return (
    <div>
      <GroupHeader group={group} />
      {group.items.map((item) => (
        <RowLink
          key={item.href}
          href={item.href}
          label={item.label}
          active={activeHref === item.href}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  );
}

function SignOutButton({ onClick }: { onClick?: () => void }) {
  if (!AUTH_CONFIGURED) return null;
  return (
    <button
      onClick={() => {
        onClick?.();
        signOut();
      }}
      className="w-full text-left rounded-md px-3 py-2 text-sm font-medium transition-colors"
      style={{ color: "var(--color-muted)" }}
    >
      Sign out
    </button>
  );
}

/* ───────────────────────────── desktop sidebar ───────────────────────────── */

export function Sidebar() {
  const path = usePathname();
  if (isChromeless(path)) return null;
  return (
    <aside
      className="app-chrome hidden md:flex flex-col w-60 shrink-0 sticky top-0 h-screen border-r"
      style={{ background: "var(--color-surface)", borderColor: "var(--color-rule)" }}
    >
      <div className="px-5 h-16 flex items-center shrink-0">
        <Wordmark />
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-4" aria-label="Main">
        {NAV_GROUPS.map((g) => (
          <GroupList key={g.id} group={g} path={path} />
        ))}
      </nav>
      <div className="shrink-0 border-t px-2 py-3 flex flex-col gap-2" style={{ borderColor: "var(--color-rule)" }}>
        {/* A background prediction run, visible from every page. */}
        <div className="px-1 empty:hidden">
          <PredictJobPill />
        </div>
        <SignOutButton />
      </div>
    </aside>
  );
}

/* ───────────────────────────── phone top bar ───────────────────────────── */

/** The active group's sibling pages as a native-style segmented tab strip:
 *  full-width, equal tabs with an underline on the active one. Groups with
 *  more pages than fit (For Nerds) scroll sideways, keeping the active tab in
 *  view. */
function SectionTabs({ group, activeHref }: { group: NavGroup; activeHref: string | null }) {
  const activeRef = useRef<HTMLAnchorElement>(null);
  const scrolls = group.items.length > 4;

  useEffect(() => {
    if (scrolls) activeRef.current?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [scrolls, activeHref]);

  return (
    <nav
      aria-label={`${group.label} pages`}
      className={`flex border-b ${scrolls ? "overflow-x-auto" : ""}`}
      style={{ borderColor: "var(--color-rule)", scrollbarWidth: "none" }}
    >
      {group.items.map((item) => {
        const active = item.href === activeHref;
        return (
          <Link
            key={item.href}
            ref={active ? activeRef : undefined}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`relative flex items-center justify-center h-11 text-sm font-medium whitespace-nowrap no-underline transition-colors ${
              scrolls ? "flex-none px-4" : "flex-1 min-w-0 px-1"
            }`}
            style={{ color: active ? "var(--color-sage)" : "var(--color-muted)" }}
          >
            {item.short ?? item.label}
            <span
              aria-hidden="true"
              className="absolute bottom-0 left-3 right-3 h-0.5 rounded-full transition-opacity"
              style={{ background: "var(--color-sage)", opacity: active ? 1 : 0 }}
            />
          </Link>
        );
      })}
    </nav>
  );
}

/** Phone top bar. No title of its own — every page already opens with its own
 *  large heading, so repeating the section name here doubled it. What remains:
 *  Back on detail routes, the section's page tabs, and the background-run pill
 *  (only while something is running). */
export function MobileTopBar() {
  const path = usePathname();
  const router = useRouter();
  if (isChromeless(path)) return null;

  const group = activeGroup(NAV_GROUPS, path);
  const detail = isDetailRoute(NAV_GROUPS, path);
  const activeHref = group ? activeItemHref(group.items, path) : null;
  const showTabs = !!group && group.items.length > 1 && !detail;

  return (
    <header
      className="app-chrome md:hidden sticky top-0 z-50"
      style={{
        background: "var(--color-surface)",
        paddingTop: "env(safe-area-inset-top)",
        paddingLeft: "env(safe-area-inset-left)",
        paddingRight: "env(safe-area-inset-right)",
      }}
    >
      {detail && (
        <div className="h-11 px-4 flex items-center border-b" style={{ borderColor: "var(--color-rule)" }}>
          <button
            onClick={() => router.back()}
            aria-label="Back"
            className="-ml-2 flex items-center gap-0.5 rounded-md py-1.5 pl-1 pr-2 text-sm font-medium"
            style={{ color: "var(--color-sage)" }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {group?.label ?? "Back"}
          </button>
        </div>
      )}
      {showTabs && <SectionTabs group={group} activeHref={activeHref} />}
      {/* Collapses to nothing when no run is in flight (the pill renders null). */}
      <div className="px-4 py-1.5 flex justify-end empty:hidden">
        <PredictJobPill />
      </div>
    </header>
  );
}

/* ───────────────────────────── phone tab bar ───────────────────────────── */

/** The last page visited in each group, so a tab returns you where you were
 *  (in-memory only — it resets on reload, which is fine for a nav nicety). */
const lastInGroup: Record<string, string> = {};

function TabButton({
  label,
  icon,
  active,
  href,
  onClick,
  scroll = true,
}: {
  label: string;
  icon: IconName;
  active: boolean;
  href?: string;
  onClick?: (e: React.MouseEvent) => void;
  /** Passed to Link: false when the tab restores its own scroll position. */
  scroll?: boolean;
}) {
  const cls = "flex flex-col items-center justify-center gap-0.5 h-14 text-[11px] font-medium no-underline";
  const style = { color: active ? "var(--color-sage)" : "var(--color-muted)" };
  const body = (
    <>
      <Icon name={icon} size={22} />
      {label}
    </>
  );
  return href ? (
    <Link
      href={href}
      scroll={scroll}
      onClick={onClick}
      className={cls}
      style={style}
      aria-current={active ? "page" : undefined}
    >
      {body}
    </Link>
  ) : (
    <button onClick={onClick} className={cls} style={style} aria-expanded={active}>
      {body}
    </button>
  );
}

/* Per-page scroll memory for the tab bar, like a native tab bar: switching
   tabs returns each one to where you left it. In-memory only (resets on
   reload). `pendingRestore` is captured at tap time — before the new page can
   clamp window.scrollY and overwrite the saved value. */
const scrollByPath: Record<string, number> = {};
let pendingRestore: { path: string; y: number } | null = null;
let restoring = false;

function useTabScrollMemory(path: string) {
  // Record the current page's offset as the reader scrolls.
  useEffect(() => {
    let raf = 0;
    const onScroll = () => {
      if (restoring || raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        scrollByPath[path] = window.scrollY;
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [path]);

  // After a tab switch lands, restore that page's offset. The page may still be
  // streaming in, so wait (up to ~1s) until it is tall enough to scroll there.
  useEffect(() => {
    const target = pendingRestore;
    if (!target || target.path !== path) return;
    pendingRestore = null;
    restoring = true;
    let tries = 0;
    let raf = 0;
    const step = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      if (max >= target.y || tries++ > 60) {
        window.scrollTo(0, Math.max(0, Math.min(target.y, max)));
        restoring = false;
      } else {
        raf = requestAnimationFrame(step);
      }
    };
    step();
    return () => {
      cancelAnimationFrame(raf);
      restoring = false;
    };
  }, [path]);
}

export function TabBar() {
  const path = usePathname();
  // The sheet is open only on the path it was opened from, so ANY navigation
  // (a row tap, browser Back) closes it without a state-syncing effect.
  const [moreOpenAt, setMoreOpenAt] = useState<string | null>(null);
  const moreOpen = moreOpenAt === path;
  const setMoreOpen = (open: boolean) => setMoreOpenAt(open ? path : null);
  const group = activeGroup(NAV_GROUPS, path);

  // Remember where the reader is inside each group, and how far down each page.
  useEffect(() => {
    if (group) lastInGroup[group.id] = path;
  }, [group, path]);
  useTabScrollMemory(path);

  // Close the sheet on Escape; lock page scroll while it is open.
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMoreOpenAt(null);
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [moreOpen]);

  if (isChromeless(path)) return null;

  const moreActive = moreOpen || (!!group && !group.tab);

  return (
    <>
      {moreOpen && (
        <div className="md:hidden fixed inset-0 z-[55]" role="dialog" aria-modal="true" aria-label="More">
          <button
            aria-label="Close"
            className="absolute inset-0 w-full h-full cursor-default"
            style={{ background: "rgba(28,25,23,0.35)" }}
            onClick={() => setMoreOpen(false)}
          />
          <div
            className="app-chrome absolute inset-x-0 bottom-0 rounded-t-2xl border-t max-h-[80vh] overflow-y-auto overscroll-contain px-2 pt-2"
            style={{
              background: "var(--color-surface)",
              borderColor: "var(--color-rule)",
              paddingBottom: "calc(4.5rem + env(safe-area-inset-bottom))",
            }}
          >
            <div className="mx-auto mb-1 mt-1 h-1 w-10 rounded-full" style={{ background: "var(--color-rule)" }} />
            {MORE_GROUPS.map((g) => (
              <GroupList key={g.id} group={g} path={path} onNavigate={() => setMoreOpen(false)} />
            ))}
            <div className="mt-3 border-t pt-2" style={{ borderColor: "var(--color-rule)" }}>
              <SignOutButton onClick={() => setMoreOpen(false)} />
            </div>
          </div>
        </div>
      )}

      <nav
        aria-label="Main"
        className="app-chrome md:hidden fixed inset-x-0 bottom-0 z-[56] border-t"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-rule)",
          paddingBottom: "env(safe-area-inset-bottom)",
          paddingLeft: "env(safe-area-inset-left)",
          paddingRight: "env(safe-area-inset-right)",
        }}
      >
        <div
          className="grid"
          style={{ gridTemplateColumns: `repeat(${TAB_GROUPS.length + 1}, minmax(0, 1fr))` }}
        >
          {TAB_GROUPS.map((g) => {
            const active = !moreOpen && group?.id === g.id;
            // Tapping the tab you're on pops back to the group's root, like a
            // native tab bar; otherwise it returns to where you left off.
            const href = active ? g.items[0].href : lastInGroup[g.id] ?? g.items[0].href;
            const onClick = (e: React.MouseEvent) => {
              if (active && path === href) {
                // Already at this tab's root: tapping it again scrolls to the top.
                e.preventDefault();
                window.scrollTo({ top: 0, behavior: "smooth" });
              } else if (!active) {
                pendingRestore = { path: href, y: scrollByPath[href] ?? 0 };
              }
            };
            return (
              <TabButton
                key={g.id}
                label={g.label}
                icon={g.icon}
                active={active}
                href={href}
                onClick={onClick}
                scroll={active}
              />
            );
          })}
          <TabButton label="More" icon="more" active={moreActive} onClick={() => setMoreOpen(!moreOpen)} />
        </div>
      </nav>
    </>
  );
}

/* ───────────────────────────── add-a-book button ───────────────────────────── */

/** Routes where a floating "+" would be redundant or in the way: the add flow
 *  itself, the import flow, and the onboarding wizard. */
const NO_FAB_PREFIXES = ["/add-book", "/import", "/welcome"];

/** Phone-only floating action button for the most common write: Add a Book.
 *  Sits above the tab bar, clear of the home indicator. Dropped on the
 *  read-only build (where /add-book doesn't exist). */
export function AddBookFab() {
  const path = usePathname();
  if (READONLY || isChromeless(path)) return null;
  if (NO_FAB_PREFIXES.some((p) => path === p || path.startsWith(p + "/"))) return null;
  return (
    <Link
      href="/add-book"
      aria-label="Add a book"
      className="app-chrome md:hidden fixed z-50 flex items-center justify-center w-14 h-14 rounded-full no-underline transition-transform active:scale-95"
      style={{
        right: "max(1rem, env(safe-area-inset-right))",
        bottom: "calc(4.5rem + env(safe-area-inset-bottom))",
        background: "var(--color-sage)",
        color: "var(--color-surface)",
        boxShadow: "0 4px 14px rgba(28, 131, 85, 0.35), 0 1px 3px rgba(26, 23, 20, 0.12)",
      }}
    >
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" />
      </svg>
    </Link>
  );
}
