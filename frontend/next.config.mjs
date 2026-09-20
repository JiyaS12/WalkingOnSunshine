import process from "node:process";

/** @type {import('next').NextConfig} */
const apiProxyTarget = process.env.API_PROXY_TARGET?.replace(/\/$/, "");

const nextConfig = {
  experimental: {
    // must cover the backend's 100 MB upload cap; the default 10 MB truncates
    // multipart bodies mid-stream and the proxied request fails with a 500
    proxyClientMaxBodySize: "110mb",
  },
  async rewrites() {
    if (!apiProxyTarget) return [];
    return [{ source: "/api/:path*", destination: `${apiProxyTarget}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Permissions-Policy",
            value: "camera=(self), microphone=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
