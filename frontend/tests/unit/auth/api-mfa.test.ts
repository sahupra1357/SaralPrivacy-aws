import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { POST as enroll } from "@/app/api/auth/mfa/enroll/route";
import { POST as verify } from "@/app/api/auth/mfa/verify/route";

function req(body: unknown, cookie?: string) {
  const headers = new Headers({ "content-type": "application/json" });
  if (cookie) headers.set("cookie", cookie);
  return new NextRequest(new URL("http://localhost:3000/api/auth/mfa/verify"), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

function backend(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    }),
  );
}

function cookieHeader(res: Response, name: string): string | undefined {
  return res.headers.getSetCookie().find((c) => c.startsWith(`${name}=`));
}

const PENDING = "mfa_pending=pending.jwt.value";

describe("POST /api/auth/mfa/enroll", () => {
  beforeEach(() => {
    process.env.BACKEND_URL = "http://backend:8000";
  });

  it("sends the pending cookie as the bearer and returns the QR, secret and factor id", async () => {
    const fetchMock = backend(200, {
      factorId: "user-uuid",
      qr: "data:image/svg+xml;base64,PHN2Zz4=",
      secret: "JBSWY3DPEHPK3PXP",
      otpauth_uri: "otpauth://totp/SaralPrivacy:a@b.c?issuer=SaralPrivacy",
    });

    const res = await enroll(req({}, PENDING));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend:8000/api/v1/auth/mfa/enroll");
    expect((init!.headers as Record<string, string>)["Authorization"]).toBe("Bearer pending.jwt.value");
    expect(await res.json()).toEqual({
      factorId: "user-uuid",
      qr: "data:image/svg+xml;base64,PHN2Zz4=",
      secret: "JBSWY3DPEHPK3PXP",
    });
  });

  it("answers 401 with step:login when the pending cookie is gone, without calling the backend", async () => {
    const fetchMock = vi.spyOn(global, "fetch");

    const res = await enroll(req({}));

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({
      error: "Session expired. Please sign in again.",
      step: "login",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("flattens the backend's 409 detail object so the page can branch on step", async () => {
    backend(409, {
      detail: { error: "A verification app is already set up for this account.", step: "verify" },
    });

    const res = await enroll(req({}, PENDING));

    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({
      error: "A verification app is already set up for this account.",
      step: "verify",
    });
  });
});

describe("POST /api/auth/mfa/verify", () => {
  beforeEach(() => {
    process.env.BACKEND_URL = "http://backend:8000";
  });

  it("sets the 8-hour access_token cookie, clears mfa_pending, and hides the token", async () => {
    backend(200, { success: true, access_token: "the.access.token", role: "admin", expires_in: 28800 });

    const res = await verify(req({ code: "123456" }, PENDING));
    const body = await res.json();

    expect(body).toEqual({ success: true, role: "admin" });
    expect(body.access_token).toBeUndefined();

    const access = cookieHeader(res, "access_token");
    expect(access).toContain("access_token=the.access.token");
    expect(access).toMatch(/HttpOnly/i);
    expect(access).toMatch(/SameSite=lax/i);
    expect(access).toContain("Path=/");
    expect(access).toContain("Max-Age=28800");

    const pending = cookieHeader(res, "mfa_pending");
    expect(pending).toContain("mfa_pending=;");
    expect(pending).toContain("Max-Age=0");
  });

  it("passes a wrong code straight through and sets no cookie", async () => {
    backend(401, { detail: "Code did not match. Try again." });

    const res = await verify(req({ code: "000000" }, PENDING));

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Code did not match. Try again." });
    expect(cookieHeader(res, "access_token")).toBeUndefined();
  });

  it("passes the 400 format error through", async () => {
    backend(400, { detail: "Enter the 6-digit code." });

    const res = await verify(req({ code: "12345" }, PENDING));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "Enter the 6-digit code." });
  });

  it("passes 429 with Retry-After through", async () => {
    backend(429, { detail: "Too many attempts. Try again later." }, { "retry-after": "7" });

    const res = await verify(req({ code: "123456" }, PENDING));

    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBe("7");
  });

  it("needs the pending cookie", async () => {
    const res = await verify(req({ code: "123456" }));

    expect(res.status).toBe(401);
    expect((await res.json()).step).toBe("login");
  });
});
