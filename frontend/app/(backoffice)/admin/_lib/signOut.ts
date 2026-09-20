"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { apiPost } from "@/lib/api";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth/session";

/**
 * Sidebar "Sign Out". Revokes the backend session first (best effort, like
 * /api/auth/logout), then drops the cookie and returns to the login page.
 * The old action deleted the `admin_session` cookie, which no longer exists.
 */
export async function signOut(): Promise<void> {
  const store = await cookies();
  const token = store.get(ACCESS_TOKEN_COOKIE)?.value;
  if (token) {
    await apiPost("/auth/logout", {}, { headers: { authorization: `Bearer ${token}` } }).catch(() => null);
  }
  store.delete(ACCESS_TOKEN_COOKIE);
  redirect("/admin/login");
}
