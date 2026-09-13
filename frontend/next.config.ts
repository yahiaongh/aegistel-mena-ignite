import type { NextConfig } from "next";
import path from "path";
import { loadEnvConfig } from "@next/env";

// Single source of truth for env: the repo-root .env (~/aegistel-mena-ignite/.env).
// Loaded before config/bundling so NEXT_PUBLIC_* vars reach the client bundle.
loadEnvConfig(path.resolve(process.cwd(), ".."));

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    proxyTimeout: 300_000,
  },
  async rewrites() {
    // Backend proxy target. Defaults to the same-host path used by local dev
    // and the single-container Dockerfile.hf deployment. In the multi-service
    // docker-compose topology the frontend must reach the backend service by
    // its compose network name, so compose passes AEGISTEL_BACKEND_URL as a
    // build arg (the rewrite is resolved at build time).
    const backend =
      process.env.AEGISTEL_BACKEND_URL ?? "http://127.0.0.1:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;