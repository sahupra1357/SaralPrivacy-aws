/**
 * Shared helpers for the six auth route handlers.
 *
 * These handlers exist for one reason: tokens must never reach browser JavaScript.
 * They forward to FastAPI, then set or clear HttpOnly cookies. Nothing else.
 *
 * FastAPI wraps an error as `{"detail": ...}` where the detail is either a string or the
 * `{error, step}` object the login page branches on. `flatten` turns both shapes back
 * into the `{error, step}` the pages have always read, so no page markup changed.
 */
import { NextResponse } from "next/server";

export const BACKEND_URL = () => process.env.BACKEND_URL || "http://localhost:8000";

export interface BackendResult {
  status: number;
  body: Record<string, unknown>;
  retryAfter: string | null;
}

export async function callBackend(
  path: string,
  init: { method?: string; body?: unknown; token?: string | null; ip?: string | null } = {}
): Promise<BackendResult> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (init.token) headers["Authorization"] = `Bearer ${init.token}`;
  if (init.ip) headers["X-Forwarded-For"] = init.ip;

  const res = await fetch(`${BACKEND_URL()}/api/v1${path}`, {
    method: init.method ?? "POST",
    headers,
    ...(init.body === undefined ? {} : { body: JSON.stringify(init.body) }),
    cache: "no-store",
  });
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  return { status: res.status, body, retryAfter: res.headers.get("retry-after") };
}

/** `{detail: "msg"}` → `{error: "msg"}`; `{detail: {error, step}}` → `{error, step}`. */
export function flatten(body: Record<string, unknown>): Record<string, unknown> {
  const detail = body.detail;
  if (typeof detail === "string") return { error: detail };
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    return { ...(detail as Record<string, unknown>) };
  }
  if (Array.isArray(detail)) return { error: "Invalid request." };
  return body;
}

export function errorResponse(result: BackendResult): NextResponse {
  const res = NextResponse.json(flatten(result.body), { status: result.status });
  if (result.retryAfter) res.headers.set("Retry-After", result.retryAfter);
  return res;
}

export function clientIp(request: { headers: Headers }): string | null {
  return (
    request.headers.get("x-real-ip") ||
    request.headers.get("x-forwarded-for") ||
    null
  );
}
