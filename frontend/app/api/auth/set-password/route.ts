/**
 * POST /api/auth/set-password — complete an invite or a password reset.
 *
 * Body: { token, type: "invite" | "recovery", password }. No session is involved and no
 * cookie is set: the user signs in normally afterwards (password + TOTP). The backend
 * checks the token hash, marks it used and revokes every existing session.
 */
import { NextRequest, NextResponse } from "next/server";

import { callBackend, clientIp, errorResponse } from "../_backend";

export async function POST(request: NextRequest) {
  const raw = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  // Links already in inboxes carry token_hash; accept both spellings.
  const body = {
    token: raw.token ?? raw.token_hash ?? "",
    type: raw.type ?? "",
    password: raw.password ?? "",
  };

  const result = await callBackend("/auth/set-password", { body, ip: clientIp(request) });
  if (result.status !== 200) return errorResponse(result);
  return NextResponse.json({ success: true });
}
