import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Next 16 Proxy (the renamed `middleware` convention — see the v16 upgrade
 * guide; the `edge` runtime is NOT supported here, proxy runs on `nodejs`).
 *
 * Two jobs, only when Supabase is configured:
 *   1. Refresh the Supabase auth cookie on every navigation (rotates the access
 *      token near expiry) and write it back onto both the request (so the SSR
 *      render downstream reads the fresh token) and the response.
 *   2. Gate the app behind login — an unauthenticated request to any app route
 *      is redirected to /login; /login itself is exempt.
 *
 * LATENCY: this runs BEFORE the page render on EVERY navigation, so whatever it
 * does is added to every tab switch. It used to call `getUser()`, which is a round
 * trip to the Supabase auth server each time. `getClaims()` verifies the JWT
 * locally against the cached JWKS instead — this project signs with ES256, i.e.
 * asymmetric keys, which is the case getClaims handles without a network call. It
 * still refreshes a near-expiry session first, so job 1 is unaffected, and on a
 * symmetric-secret project it silently falls back to a server call — the old
 * behaviour, never a weaker check.
 *
 * The trade is that `claims.user_metadata` is the metadata as of token issue, not
 * as of now. That matters for exactly one flag here (`onboarded`), so the welcome
 * wizard forces a token refresh right after it writes the flag — see
 * app/welcome/WelcomeClient.tsx. The backend already reads user_metadata from the
 * same JWT claims, so this makes the two agree rather than introducing a new
 * staleness.
 *
 * When the Supabase env is ABSENT (local dev + the static public build, which
 * set neither NEXT_PUBLIC_SUPABASE_URL nor _ANON_KEY) this is a transparent
 * pass-through, so those deployments run exactly as before — no auth.
 */
export async function proxy(request: NextRequest) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return NextResponse.next(); // auth off → do nothing

  let response = NextResponse.next({ request });

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        // Mirror the refreshed cookies onto the request (for the downstream SSR
        // render) and the response (for the browser), per the @supabase/ssr
        // pattern. Must not run any logic between createServerClient and
        // getUser() below, or the session can desync.
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  // Locally-verified JWT claims (see the note above). `sub` present == signed in.
  const { data: claimsData } = await supabase.auth.getClaims();
  const claims = claimsData?.claims;
  const user = claims?.sub ? claims : null;

  const path = request.nextUrl.pathname;
  const isLogin = path === "/login" || path.startsWith("/login/");
  const isWelcome = path === "/welcome" || path.startsWith("/welcome/");
  // The public "Try it" prediction demo (app/try) is intentionally reachable
  // WITHOUT an account — it's the one un-gated app route, so a resume/portfolio
  // link works with no sign-up. It calls only the read-only, tenant-fixed
  // /api/demo/predict endpoint. Exempt it from BOTH the login redirect and the
  // onboarding redirect so it always renders regardless of auth state.
  const isTry = path === "/try" || path.startsWith("/try/");
  // First-run signal: a Supabase user_metadata flag set when the tutorial is
  // completed (see app/welcome). Absent → treat as not-yet-onboarded.
  const onboarded =
    (user?.user_metadata as { onboarded?: boolean } | undefined)?.onboarded === true;

  if (!user && !isLogin && !isTry) {
    const redirectUrl = request.nextUrl.clone();
    redirectUrl.pathname = "/login";
    redirectUrl.search = "";
    redirectUrl.searchParams.set("next", path);
    return NextResponse.redirect(redirectUrl);
  }

  if (user && isLogin) {
    const dest = request.nextUrl.clone();
    dest.pathname = onboarded ? "/" : "/welcome";
    dest.search = "";
    return NextResponse.redirect(dest);
  }

  // Signed in but hasn't finished first-run setup → send to the tutorial. It is
  // exempt from this (so it can render), and completing it sets the flag above.
  // /try is exempt too, so the public demo always renders.
  if (user && !onboarded && !isWelcome && !isTry) {
    const welcome = request.nextUrl.clone();
    welcome.pathname = "/welcome";
    welcome.search = "";
    return NextResponse.redirect(welcome);
  }

  return response;
}

export const config = {
  // Run on every route EXCEPT Next internals, the /data static snapshots, and
  // asset files — otherwise the gate would block CSS/JS/images/JSON from loading.
  //
  // `sw.js` is listed explicitly: the extension allowlist below has no `.js`
  // (bundled JS lives under the excluded `_next/static`), so without this the
  // service worker would be auth-gated and served a redirect to /login, and the
  // PWA would never install for a signed-in user. `/manifest.json` needs no
  // entry — it is already covered by `json$`, which is why the manifest is a
  // static file in public/ rather than an app/manifest.ts route.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|sw.js|data/|.*\\.(?:png|svg|jpg|jpeg|gif|webp|ico|json|txt|xml)$).*)",
  ],
};
