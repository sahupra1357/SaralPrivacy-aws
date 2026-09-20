/**
 * Catch-all proxy: the browser calls /api/proxy/api/v1/<...> and this forwards to the
 * FastAPI backend, attaching the HttpOnly `access_token` cookie as a bearer header.
 * Tokens never reach browser JavaScript. Streaming bodies (chat) pass straight through.
 */
import { type NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
export const ACCESS_COOKIE = "access_token";

const PASS_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "cache-control",
  "etag",
  "retry-after",
  "x-content-type-options",
];

// svix-* carry the Resend webhook signature; the backend verifies it over the raw body.
const PASS_REQUEST_HEADERS = [
  "accept",
  "accept-language",
  "user-agent",
  "x-forwarded-for",
  "svix-id",
  "svix-timestamp",
  "svix-signature",
  // Visitor location added by CloudFront (AWS phase); the backend reads these for the
  // city/country/region columns that Vercel's x-vercel-ip-* headers used to fill.
  "cloudfront-viewer-address",
  "cloudfront-viewer-city",
  "cloudfront-viewer-country",
  "cloudfront-viewer-country-region",
];

type Params = Promise<{ path: string[] }>;

export async function proxyRequest(request: NextRequest, path: string[]): Promise<NextResponse> {
  const url = `${BACKEND_URL}/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const h of PASS_REQUEST_HEADERS) {
    const v = request.headers.get(h);
    if (v) headers.set(h, v);
  }
  // Forward the real client address so backend rate limiting keys on the visitor.
  const ip = request.headers.get("x-real-ip") || request.headers.get("x-forwarded-for");
  if (ip) headers.set("x-forwarded-for", ip);

  const token = request.cookies.get(ACCESS_COOKIE)?.value;
  if (token) headers.set("authorization", `Bearer ${token}`);

  let body: BodyInit | null = null;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const contentType = request.headers.get("content-type") || "";
    if (contentType.includes("multipart/form-data")) {
      body = await request.formData();
    } else {
      body = await request.text();
      if (contentType) headers.set("content-type", contentType);
    }
  }

  const backendRes = await fetch(url, { method: request.method, headers, body, redirect: "manual" });

  const resHeaders = new Headers();
  for (const h of PASS_RESPONSE_HEADERS) {
    const v = backendRes.headers.get(h);
    if (v) resHeaders.set(h, v);
  }
  return new NextResponse(backendRes.body, { status: backendRes.status, headers: resHeaders });
}

async function handle(request: NextRequest, ctx: { params: Params }) {
  const { path } = await ctx.params;
  return proxyRequest(request, path);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
