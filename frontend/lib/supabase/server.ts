import "server-only";

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { SUPABASE_URL, SUPABASE_ANON_KEY } from "./client";

/**
 * Server-only: the Supabase access token for the current request, read from the
 * cookie the proxy refreshes. Marked `server-only` with a static `next/headers`
 * import, so it is imported EXCLUSIVELY by server components (the page files) and
 * never by the isomorphic lib/api.ts — which is why api.ts stays out of trouble
 * in the client bundle. Pages read the token here and pass it into the api.ts
 * fetch calls.
 *
 * Returns undefined (without ever touching cookies(), so static/local builds are
 * NOT deopted to dynamic rendering) when Supabase is not configured — i.e. auth
 * is off. Otherwise returns the session's access token, or undefined if signed
 * out.
 */
export async function getServerAccessToken(): Promise<string | undefined> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return undefined;
  const store = await cookies();
  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    // Read-only here (the proxy owns cookie refresh): getAll exposes the request
    // cookies; setAll is a no-op because a Server Component cannot set cookies.
    cookies: { getAll: () => store.getAll(), setAll: () => {} },
  });
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token;
}
