import type { NextConfig } from "next";

// The browser talks to its own origin only: /api is forwarded to the backend with the prefix
// dropped, so there is no CORS and the token never crosses origins (docs/PHASE4.md)
const API = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // A reply is a stream: do not compress it, and allow a turn longer than the proxy's default
  compress: false,
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
