import { createBrowserClient } from "@supabase/ssr";

/** Supabase project URL + anon key, inlined at build from NEXT_PUBLIC_* env.
 * Present only on the hosted multi-tenant build; unset in local dev and the
 * static public snapshot (where auth is off and this module is never used). */
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/** Browser Supabase client with a cookie-backed session (shared with the server
 * via @supabase/ssr, so SSR page loads can read the same token). Used by the
 * login page, the client-side token attach in lib/api.ts, and the 401 bounce.
 * Safe to import anywhere — it never pulls in next/headers. */
export function createSupabaseBrowserClient() {
  return createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}
