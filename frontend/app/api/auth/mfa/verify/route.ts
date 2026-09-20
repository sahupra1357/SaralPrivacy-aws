/**
 * POST /api/auth/mfa/verify — step 2 of the admin login: the TOTP code.
 *
 * This is the ONLY place an access_token cookie is set. The backend mints the token and
 * inserts the app.sessions row behind it; we store the token HttpOnly and clear the
 * pending cookie. The token itself is never returned to the page.
 */
import { NextRequest, NextResponse } from "next/server";

import { callBackend, clientIp, errorResponse } from "../../_backend";
import {
  ACCESS_TOKEN_COOKIE,
  ACCESS_TOKEN_MAX_AGE,
  MFA_PENDING_COOKIE,
  MFA_PENDING_COOKIE_PATH,
  cookieOptions,
} from "@/lib/auth/session";

const EXPIRED = { error: "Session expired. Please sign in again.", step: "login" };

export async function POST(request: NextRequest) {
  const pending = request.cookies.get(MFA_PENDING_COOKIE)?.value;
  if (!pending) return NextResponse.json(EXPIRED, { status: 401 });

  const body = await request.json().catch(() => ({}));
  const result = await callBackend("/auth/mfa/verify", {
    body,
    token: pending,
    ip: clientIp(request),
  });
  if (result.status !== 200) return errorResponse(result);

  const token = result.body.access_token;
  if (typeof token !== "string" || !token) {
    return NextResponse.json({ error: "Login is not configured." }, { status: 500 });
  }

  const res = NextResponse.json({ success: true, role: result.body.role });
  res.cookies.set(ACCESS_TOKEN_COOKIE, token, cookieOptions(request, "/", ACCESS_TOKEN_MAX_AGE));
  res.cookies.set(MFA_PENDING_COOKIE, "", cookieOptions(request, MFA_PENDING_COOKIE_PATH, 0));
  return res;
}
