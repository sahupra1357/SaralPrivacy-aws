/**
 * POST /api/auth/logout — revoke the session server-side, then clear the cookies.
 *
 * The backend call is best effort: if it fails the cookies still go, so the browser is
 * signed out either way. Clearing a cookie the backend kept alive would be the wrong way
 * round, which is why the revoke is attempted first.
 */
import { NextRequest, NextResponse } from "next/server";

import { callBackend, clientIp } from "../_backend";
import {
  ACCESS_TOKEN_COOKIE,
  MFA_PENDING_COOKIE,
  MFA_PENDING_COOKIE_PATH,
  cookieOptions,
} from "@/lib/auth/session";

export async function POST(request: NextRequest) {
  const token = request.cookies.get(ACCESS_TOKEN_COOKIE)?.value;
  if (token) {
    await callBackend("/auth/logout", { body: {}, token, ip: clientIp(request) }).catch(() => null);
  }

  const res = NextResponse.json({ success: true });
  res.cookies.set(ACCESS_TOKEN_COOKIE, "", cookieOptions(request, "/", 0));
  res.cookies.set(MFA_PENDING_COOKIE, "", cookieOptions(request, MFA_PENDING_COOKIE_PATH, 0));
  return res;
}

export const DELETE = POST;
