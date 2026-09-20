import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { POST as login } from "@/app/api/auth/login/route";

function req(body: unknown, { cookie, url = "http://localhost:3000/api/auth/login" } = {} as { cookie?: string; url?: string }) {
  const headers = new Headers({ "content-type": "application/json" });
  if (cookie) headers.set("cookie", cookie);
  return new NextRequest(new URL(url), { method: "POST", headers, body: JSON.stringify(body) });
}

function backend(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    }),
  );
}

/** Set-Cookie attributes for one cookie name, from the response headers. */
function cookieHeader(res: Response, name: string): string | undefined {
  return res.headers.getSetCookie().find((c) => c.startsWith(`${name}=`));
}

describe("POST /api/auth/login", () => {
  beforeEach(() => {
    process.env.BACKEND_URL = "http://backend:8000";
  });

  it("forwards to the backend and parks the pending token in an HttpOnly path-scoped cookie", async () => {
    const fetchMock = backend(200, {
      success: true,
      step: "verify",
      role: "admin",
      pending_token: "pending.jwt.value",
    });

    const res = await login(req({ email: "a@b.c", password: "secret-password" }));
    const body = await res.json();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend:8000/api/v1/auth/login");
    expect(init!.method).toBe("POST");
    expect(JSON.parse(init!.body as string)).toEqual({ email: "a@b.c", password: "secret-password" });

    expect(body).toEqual({ success: true, step: "verify", role: "admin" });
    // The token must never reach page JavaScript.
    expect(body.pending_token).toBeUndefined();

    const cookie = cookieHeader(res, "mfa_pending");
    expect(cookie).toContain("mfa_pending=pending.jwt.value");
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    expect(cookie).toContain("Path=/api/auth/mfa");
    expect(cookie).toContain("Max-Age=600");
    expect(cookie).not.toContain("Secure"); // http in tests
  });

  it("passes the backend's 401 and its exact message straight through", async () => {
    backend(401, { detail: "Invalid credentials." });

    const res = await login(req({ email: "a@b.c", password: "nope" }));

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Invalid credentials." });
    expect(cookieHeader(res, "mfa_pending")).toBeUndefined();
  });

  it("passes 403 Access denied. through", async () => {
    backend(403, { detail: "Access denied." });

    const res = await login(req({ email: "a@b.c", password: "secret-password" }));

    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: "Access denied." });
  });

  it("passes 429 with Retry-After through", async () => {
    backend(429, { detail: "Too many attempts. Try again later." }, { "retry-after": "42" });

    const res = await login(req({ email: "a@b.c", password: "secret-password" }));

    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBe("42");
    expect(await res.json()).toEqual({ error: "Too many attempts. Try again later." });
  });

  it("forwards the caller's IP so the backend rate limits the visitor, not the frontend", async () => {
    const fetchMock = backend(200, { step: "enroll", role: "admin", pending_token: "t" });
    const headers = new Headers({ "content-type": "application/json", "x-real-ip": "9.9.9.9" });
    const request = new NextRequest(new URL("http://localhost:3000/api/auth/login"), {
      method: "POST",
      headers,
      body: JSON.stringify({ email: "a@b.c", password: "secret-password" }),
    });

    await login(request);

    expect((fetchMock.mock.calls[0][1]!.headers as Record<string, string>)["X-Forwarded-For"]).toBe("9.9.9.9");
  });

  it("refuses to report success when the backend returned no pending token", async () => {
    backend(200, { step: "verify", role: "admin" });

    const res = await login(req({ email: "a@b.c", password: "secret-password" }));

    expect(res.status).toBe(500);
    expect(cookieHeader(res, "mfa_pending")).toBeUndefined();
  });
});
