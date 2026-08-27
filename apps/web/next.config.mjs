import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@somna/event-schema"],
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  async rewrites() {
    // Priority: explicit NEXT_PUBLIC_API_BASE > Docker internal agent-core > dev localhost.
    const apiBase =
      process.env.NEXT_PUBLIC_API_BASE ||
      process.env.AGENT_CORE_URL ||
      "http://localhost:8000";
    return {
      // Explicit App Router handlers (notably auth and SSE) must run first.
      // A flat rewrite array is evaluated before dynamic routes, which would
      // bypass the auth handler responsible for setting the HttpOnly cookie.
      fallback: [
        // In dev we proxy straight to agent-core; in prod BFF handles this.
        { source: "/api/v1/:path*", destination: `${apiBase}/v1/:path*` },
      ],
    };
  },
  // 与 tsconfig paths 的 @/* 对齐；避免部分环境下仅读 tsconfig 失败导致 Module not found
  webpack: (config) => {
    config.resolve.alias = {
      ...(config.resolve.alias ?? {}),
      "@": __dirname,
    };
    return config;
  },
};

export default nextConfig;
