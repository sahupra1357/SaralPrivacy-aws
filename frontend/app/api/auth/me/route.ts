/**
 * GET /api/auth/me — who the access_token cookie belongs to.
 * The backend re-checks the app.sessions row, so a revoked session answers 401 here too.
 */
import { NextRequest, NextResponse } from "next/server";

import { callBackend, clientIp, errorResponse } from "../_backend";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth/session";

export async function GET(request: NextRequest) {
  const token = request.cookies.get(ACCESS_TOKEN_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ error: "Session expired. Please sign in again." }, { status: 401 });
  }

  const result = await callBackend("/auth/me", {
    method: "GET",
    token,
    ip: clientIp(request),
  });
  if (result.status !== 200) return errorResponse(result);
  return NextResponse.json(result.body);
}
