/**
 * POST /api/auth/mfa/enroll — first-login TOTP enrollment.
 *
 * Needs the MFA-pending cookie from /api/auth/login. Returns the QR code (SVG data URL)
 * and the secret for manual entry. The backend refuses when a verified factor already
 * exists — adding a second factor is not something the login flow may do.
 */
import { NextRequest, NextResponse } from "next/server";

import { callBackend, clientIp, errorResponse } from "../../_backend";
import { MFA_PENDING_COOKIE } from "@/lib/auth/session";

const EXPIRED = { error: "Session expired. Please sign in again.", step: "login" };

export async function POST(request: NextRequest) {
  const pending = request.cookies.get(MFA_PENDING_COOKIE)?.value;
  if (!pending) return NextResponse.json(EXPIRED, { status: 401 });

  const result = await callBackend("/auth/mfa/enroll", {
    body: {},
    token: pending,
    ip: clientIp(request),
  });
  if (result.status !== 200) return errorResponse(result);

  return NextResponse.json({
    factorId: result.body.factorId,
    qr: result.body.qr,
    secret: result.body.secret,
  });
}
