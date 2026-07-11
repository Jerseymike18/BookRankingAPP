"use client"; // Error boundaries must be Client Components

import { useEffect, useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * Root error boundary (Next 16 `error.tsx`). Catches any error thrown while a
 * page's Server Component renders. The important case is a backend 401 when the
 * session token is missing/expired: `lib/api.ts` throws on it, and without this
 * boundary that dead-ends as the raw "This page couldn't load" 500.
 *
 * On the hosted (auth-configured) build it tries to recover gracefully:
 *   - no valid session   -> send the user to /login, preserving where they were;
 *   - valid session      -> the render most likely hit a stale server-side
 *                           cookie, so re-fetch the segment ONCE with the token
 *                           the browser just refreshed (unstable_retry).
 * A short sessionStorage guard prevents a retry loop when the failure isn't auth
 * (e.g. the backend is actually down) — after one attempt it shows the manual
 * fallback instead. When auth is off (local dev / static showcase) it skips all
 * of that and just shows the fallback with a retry button.
 *
 * Styling reuses the existing Fable tokens + the login card pattern — no new
 * visual styles.
 */

const AUTH_CONFIGURED =
  !!process.env.NEXT_PUBLIC_SUPABASE_URL &&
  !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

const RETRY_KEY = "trl:err-retried-at";
const RETRY_WINDOW_MS = 15_000;

export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  // On the auth build we first try to recover, so start in the quiet
  // "checking" state; with auth off there's nothing to check.
  const [checking, setChecking] = useState(AUTH_CONFIGURED);

  useEffect(() => {
    console.error(error);
    if (!AUTH_CONFIGURED) return;

    let cancelled = false;
    (async () => {
      let hasSession = false;
      try {
        // getSession() refreshes the access token when the refresh token is
        // still valid, writing a fresh cookie the next server render can read.
        const { data } = await createSupabaseBrowserClient().auth.getSession();
        hasSession = !!data.session;
      } catch {
        hasSession = false;
      }
      if (cancelled) return;

      if (!hasSession) {
        const path =
          window.location.pathname + window.location.search + window.location.hash;
        window.location.assign(`/login?next=${encodeURIComponent(path)}`);
        return;
      }

      // Live session -> the failure was most likely a stale server-side cookie.
      // Re-fetch the segment once; if we already retried moments ago and we're
      // still here, it isn't an auth problem -> fall through to the fallback.
      const last = Number(sessionStorage.getItem(RETRY_KEY) || "0");
      if (Date.now() - last > RETRY_WINDOW_MS) {
        sessionStorage.setItem(RETRY_KEY, String(Date.now()));
        unstable_retry();
        return; // stay in "checking": either it recovers or it re-errors here
      }
      setChecking(false);
    })();

    return () => {
      cancelled = true;
    };
    // Re-evaluate whenever a new error arrives (e.g. a failed retry); unstable_retry
    // is intentionally not a dep — we always call the latest within one error.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error]);

  return (
    <div className="max-w-sm mx-auto w-full">
      <div
        className="rounded-xl p-6 mt-8"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <h1 className="font-display font-semibold text-lg mb-1" style={{ color: "var(--color-ink)" }}>
          {checking ? "Reconnecting…" : "Something went wrong"}
        </h1>
        <p className="text-xs mb-5" style={{ color: "var(--color-muted)" }}>
          {checking
            ? "Checking your session."
            : "The page couldn’t load. This is usually temporary."}
        </p>

        {!checking && (
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => {
                setChecking(false);
                unstable_retry();
              }}
              className="px-6 py-3 rounded-xl font-semibold text-sm transition-colors"
              style={{ background: "var(--color-sage)", color: "#fff" }}
            >
              Try again
            </button>
            <a
              href="/login"
              className="text-sm font-medium underline"
              style={{ color: "var(--color-sage)" }}
            >
              Sign in
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
