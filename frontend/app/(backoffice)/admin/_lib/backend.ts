/**
 * Server-side calls from admin server components / server actions to the backend,
 * carrying the signed-in admin's HttpOnly access token as a bearer header.
 */
import "server-only";

import { cookies } from "next/headers";

import { apiGet, apiPost } from "@/lib/api";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth/session";

export async function authHeaders(): Promise<Record<string, string>> {
  const token = (await cookies()).get(ACCESS_TOKEN_COOKIE)?.value;
  return token ? { authorization: `Bearer ${token}` } : {};
}

/** Admin data is never cached: every render reads live rows. */
export async function adminGet<T>(path: string): Promise<T> {
  return apiGet<T>(path, { headers: await authHeaders(), revalidate: 0 });
}

export async function adminPost<T>(path: string, json: unknown): Promise<T> {
  return apiPost<T>(path, json, { headers: await authHeaders() });
}
