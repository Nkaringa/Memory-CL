/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `output: 'standalone'` lets ui/Dockerfile.production ship a runtime
  // image without node_modules — only the .next/standalone bundle and
  // the static assets are copied into the final stage.
  output: "standalone",
  // Backend lives on a separate origin during dev; rewrite so the
  // browser can call /api/* without CORS even when the FastAPI service
  // isn't co-located.
  async rewrites() {
    const backend = process.env.MEMORY_CL_BACKEND_URL ?? "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/:path*` },
    ];
  },
  experimental: {
    typedRoutes: true,
  },
};
export default nextConfig;
