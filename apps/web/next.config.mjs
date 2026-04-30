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
    return [
      // In dev we proxy straight to agent-core; in prod BFF handles this.
      { source: "/api/v1/:path*", destination: `${apiBase}/v1/:path*` },
    ];
  },
};

export default nextConfig;
