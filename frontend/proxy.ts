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

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isLogin = path === "/login" || path.startsWith("/login/");
  const isWelcome = path === "/welcome" || path.startsWith("/welcome/");
  // First-run signal: a Supabase user_metadata flag set when the tutorial is
  // completed (see app/welcome). Absent → treat as not-yet-onboarded.
  const onboarded = user?.user_metadata?.onboarded === true;

  if (!user && !isLogin) {
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
  if (user && !onboarded && !isWelcome) {
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
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|data/|.*\\.(?:png|svg|jpg|jpeg|gif|webp|ico|json|txt|xml)$).*)",
  ],
};
