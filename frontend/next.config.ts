import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// next-intl request config lives at the default location, ./i18n/request.ts
const withNextIntl = createNextIntlPlugin();

// Uploaded files (blog infographics) are served from S3-compatible storage: MinIO on
// http://localhost:9000 locally, S3/CloudFront over https in production. Resolved at
// build time from PUBLIC_ASSET_BASE_URL (a Docker build arg) so the CSP and the image
// allow-list name exactly that origin.
function assetOrigin(): URL | null {
  try {
    const raw = process.env.PUBLIC_ASSET_BASE_URL;
    return raw ? new URL(raw) : null;
  } catch {
    return null;
  }
}
const ASSET = assetOrigin();
// `https:` already covers production storage; a plain-http origin (local MinIO) must be named.
const ASSET_IMG_SRC = ASSET && ASSET.protocol === "http:" ? ` ${ASSET.origin}` : "";

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image (infra/), and the workspace root
  // pinned to this directory — without the pin, Next walks up for a lockfile and the
  // standalone output lands at .next/standalone/<nested path>/server.js instead of
  // .next/standalone/server.js, which the Dockerfile expects.
  //
  // Both are OFF on Vercel. Its Root Directory is `frontend`, so it expects traced paths
  // relative to the REPO root ("frontend/.next/..."); pinning the tracing root here makes
  // them relative to frontend/ instead, and the deploy dies collecting outputs with
  // ENOENT /vercel/path0/.next/package.json. Vercel needs no pin: the lockfile is here.
  output: process.env.VERCEL ? undefined : "standalone",
  ...(process.env.VERCEL ? {} : { turbopack: { root: __dirname }, outputFileTracingRoot: __dirname }),
  images: {
    remotePatterns: ASSET
      ? [
          {
            protocol: ASSET.protocol.replace(":", "") as "http" | "https",
            hostname: ASSET.hostname,
            ...(ASSET.port ? { port: ASSET.port } : {}),
            pathname: `${ASSET.pathname.replace(/\/$/, "")}/**`,
          },
        ]
      : [],
  },
  // External callers that cannot change their URL (Resend webhook) reach the backend
  // through the proxy route. Browser code calls /api/proxy/... directly.
  async rewrites() {
    // Evaluated at build time; the compose service name is the default.
    const backend = process.env.BACKEND_URL || "http://backend:8000";
    return [
      { source: "/api/webhooks/resend", destination: "/api/proxy/api/v1/webhooks/resend" },
      // n8n calls /api/briefings/today with a bearer secret, and approval emails
      // already in inboxes link to /api/briefings/approve. A direct rewrite keeps the
      // Authorization header and Location redirects intact, which the proxy route does not.
      { source: "/api/briefings/:path*", destination: `${backend}/api/v1/briefings/:path*` },
    ];
  },
  async redirects() {
    return [
      { source: '/subscribe', destination: '/#newsletter', permanent: false },
      { source: '/unsubscribe', destination: '/consent-preferences', permanent: true },
      { source: '/rights/access', destination: '/privacy#data-rights', permanent: true },
      { source: '/rights/erasure', destination: '/privacy#data-rights', permanent: true },
      { source: '/webinars', destination: '/resources', permanent: false },
      { source: '/resources/dpdpa-explainer-for-founders', destination: '/learn/what-is-dpdpa', permanent: true },
      { source: '/resources/consent-notice-template-forms', destination: '/resources', permanent: true },
      { source: '/resources/dpdpa-compliance-checklist-2025', destination: '/resources', permanent: true },
      { source: '/resources/privacy-notice-template-b2c', destination: '/resources', permanent: true },
      { source: '/resources/data-rights-request-form-template', destination: '/resources', permanent: true },
    ]
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
          { key: 'Content-Security-Policy', value: `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:${ASSET_IMG_SRC}; connect-src 'self';` },
        ],
      },
    ]
  },
};

export default withNextIntl(nextConfig);
