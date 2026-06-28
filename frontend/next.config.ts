import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
