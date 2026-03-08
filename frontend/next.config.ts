import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const API_BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  // Use separate output dir for production builds so `next build`
  // doesn't nuke the dev server's .next/ directory mid-HMR.
  distDir: process.env.NODE_ENV === "production" ? ".next-build" : ".next",
  // Standalone output for Docker — produces a minimal self-contained server.
  // Set NEXT_OUTPUT=standalone in Dockerfile; unset locally to keep `next start` working.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // Ensure next-intl message files are included in standalone output.
  // They are read at runtime via fs.readdirSync() and cannot be statically traced.
  outputFileTracingIncludes: {
    "/*": ["./messages/**/*.json"],
  },
  allowedDevOrigins: ["192.168.9.114"],
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_BACKEND}/api/:path*` },
      { source: "/uploads/:path*", destination: `${API_BACKEND}/uploads/:path*` },
    ];
  },
};

export default withNextIntl(nextConfig);
