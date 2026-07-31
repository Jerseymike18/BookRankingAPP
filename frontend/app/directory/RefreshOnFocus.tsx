"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

/** Re-run the directory's server fetch when the tab regains focus, so a
 * just-published (or newly-made-private) profile shows up without a manual
 * reload. `router.refresh()` re-renders the server component in place — no full
 * navigation, no lost scroll. Throttled (default 8s) so rapid focus/blur can't
 * hammer the backend — the directory route is rate-limited per viewer. */
export default function RefreshOnFocus({ minIntervalMs = 8000 }: { minIntervalMs?: number }) {
  const router = useRouter();
  const lastRef = useRef(0);

  useEffect(() => {
    const maybeRefresh = () => {
      // Ignore the visibilitychange that fires when the tab goes HIDDEN.
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastRef.current < minIntervalMs) return;
      lastRef.current = now;
      router.refresh();
    };
    window.addEventListener("focus", maybeRefresh);
    document.addEventListener("visibilitychange", maybeRefresh);
    return () => {
      window.removeEventListener("focus", maybeRefresh);
      document.removeEventListener("visibilitychange", maybeRefresh);
    };
  }, [router, minIntervalMs]);

  return null;
}
