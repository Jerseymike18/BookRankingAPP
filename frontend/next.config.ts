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

  // The fiction views moved under /fiction/* when the app was split into
  // Fiction / Nonfiction sections. Keep the old top-level paths working.
  async redirects() {
    return [
      { source: "/rankings", destination: "/fiction/rankings", permanent: false },
      { source: "/tier-list", destination: "/fiction/tier-list", permanent: false },
      { source: "/series", destination: "/fiction/series", permanent: false },
      { source: "/timeline", destination: "/fiction/timeline", permanent: false },
      { source: "/reading", destination: "/fiction/reading", permanent: false },
    ];
  },
};

export default nextConfig;
