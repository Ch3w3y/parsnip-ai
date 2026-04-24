/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // API proxy routes are handled by src/app/api/ route handlers at runtime,
  // not by rewrites (which are baked at build time and break in Docker).
};

module.exports = nextConfig;