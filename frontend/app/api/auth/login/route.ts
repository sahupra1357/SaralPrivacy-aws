/**
 * POST /api/auth/login — step 1 of the admin login.
 *
 * A correct password is NOT a session. The backend answers with a 10-minute pending
 * token; we park it in a path-scoped HttpOnly cookie and tell the page whether to
 * enroll a TOTP factor or verify one. The access_token cookie is only ever minted by
 * /api/auth/mfa/verify.
 */
import { NextRequest, NextResponse } from "next/server";

import { callBackend, clientIp, errorResponse } from "../_backend";
import { MFA_PENDING_COOKIE, MFA_PENDING_COOKIE_PATH, MFA_PENDING_MAX_AGE, cookieOptions } from "@/lib/auth/session";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const result = await callBackend("/auth/login", { body, ip: clientIp(request) });

  if (result.status !== 200) return errorResponse(result);

  const pendingToken = result.body.pending_token;
  if (typeof pendingToken !== "string" || !pendingToken) {
    return NextResponse.json({ error: "Login is not configured." }, { status: 500 });
  }

  // The pending token never reaches page JavaScript — only this cookie carries it.
  const res = NextResponse.json({
    success: true,
    step: result.body.step,
    role: result.body.role,
  });
  res.cookies.set(
    MFA_PENDING_COOKIE,
    pendingToken,
    cookieOptions(request, MFA_PENDING_COOKIE_PATH, MFA_PENDING_MAX_AGE)
  );
  return res;
}
