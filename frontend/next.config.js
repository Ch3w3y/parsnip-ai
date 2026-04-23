/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.AGENT_INTERNAL_URL || process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8000"}/v1/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;