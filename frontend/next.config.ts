import type { NextConfig } from "next";

// Baseline security headers applied to every response. CSP is intentionally
// permissive on styles/images ('unsafe-inline' for Tailwind's injected styles and
// KaTeX CSS; data: for inline SVG/PNG) but blocks framing and object embeds. Tune
// `connect-src` if the API/Supabase origins change.
//
// `next dev` (HMR + React Refresh) needs 'unsafe-eval', so it's added only in
// development — production keeps the tighter script-src.
const isDev = process.env.NODE_ENV !== "production";
const scriptSrc = isDev
  ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
  : "script-src 'self' 'unsafe-inline'";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "base-uri 'self'",
      "frame-ancestors 'none'",
      "object-src 'none'",
      "img-src 'self' data:",
      "style-src 'self' 'unsafe-inline'",
      "font-src 'self' data:",
      scriptSrc,
      "connect-src 'self' https://*.supabase.co https://*.up.railway.app",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },

  // The Fiction / Nonfiction section split was collapsed into an in-page
  // Fiction / Nonfiction / All toggle on each view (2026 nav redesign). The old
  // per-type paths now redirect to the single canonical route, seeding the
  // toggle via ?type=. Read Queue is de-duplicated to one route (under Reading);
  // the old `/nonfiction/read-queue` folds in with ?type=nonfiction.
  async redirects() {
    return [
      { source: "/fiction/rankings", destination: "/rankings?type=fiction", permanent: false },
      { source: "/nonfiction/rankings", destination: "/rankings?type=nonfiction", permanent: false },
      { source: "/fiction/tier-list", destination: "/tier-list?type=fiction", permanent: false },
      { source: "/nonfiction/tier-list", destination: "/tier-list?type=nonfiction", permanent: false },
      { source: "/fiction/series", destination: "/series?type=fiction", permanent: false },
      { source: "/nonfiction/series", destination: "/series?type=nonfiction", permanent: false },
      { source: "/fiction/timeline", destination: "/timeline?type=fiction", permanent: false },
      { source: "/nonfiction/timeline", destination: "/timeline?type=nonfiction", permanent: false },
      { source: "/fiction/reading", destination: "/reading?type=fiction", permanent: false },
      { source: "/nonfiction/reading", destination: "/reading?type=nonfiction", permanent: false },
      { source: "/nonfiction/read-queue", destination: "/read-queue?type=nonfiction", permanent: false },
    ];
  },
};

export default nextConfig;
