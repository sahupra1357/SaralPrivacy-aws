import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET as me } from "@/app/api/auth/me/route";
import { POST as logout } from "@/app/api/auth/logout/route";
import { POST as setPassword } from "@/app/api/auth/set-password/route";

function get(path: string, cookie?: string) {
  const headers = new Headers();
  if (cookie) headers.set("cookie", cookie);
  return new NextRequest(new URL(`http://localhost:3000${path}`), { method: "GET", headers });
}

function post(path: string, body: unknown, cookie?: string) {
  const headers = new Headers({ "content-type": "application/json" });
  if (cookie) headers.set("cookie", cookie);
  return new NextRequest(new URL(`http://localhost:3000${path}`), {
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

const SESSION = "access_token=the.access.token";

beforeEach(() => {
  process.env.BACKEND_URL = "http://backend:8000";
});

describe("GET /api/auth/me", () => {
  it("forwards the cookie as a bearer and returns the user shape", async () => {
    const fetchMock = backend(200, { id: "u1", email: "a@b.c", role: "admin", name: "Desk" });

    const res = await me(get("/api/auth/me", SESSION));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend:8000/api/v1/auth/me");
    expect(init!.method).toBe("GET");
    expect((init!.headers as Record<string, string>)["Authorization"]).toBe("Bearer the.access.token");
    expect(await res.json()).toMatchObject({ email: "a@b.c", role: "admin" });
  });

  it("answers 401 without a cookie and never calls the backend", async () => {
    const fetchMock = vi.spyOn(global, "fetch");

    const res = await me(get("/api/auth/me"));

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Session expired. Please sign in again." });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes a revoked session's 401 through", async () => {
    backend(401, { detail: "Session expired. Please sign in again." });

    const res = await me(get("/api/auth/me", SESSION));

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Session expired. Please sign in again." });
  });
});

describe("POST /api/auth/logout", () => {
  it("revokes the session server-side and clears both cookies", async () => {
    const fetchMock = backend(200, { success: true });

    const res = await logout(post("/api/auth/logout", {}, SESSION));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend:8000/api/v1/auth/logout");
    expect((init!.headers as Record<string, string>)["Authorization"]).toBe("Bearer the.access.token");

    expect(await res.json()).toEqual({ success: true });
    expect(cookieHeader(res, "access_token")).toContain("Max-Age=0");
    expect(cookieHeader(res, "mfa_pending")).toContain("Max-Age=0");
  });

  it("still clears the cookies when the backend call fails", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("backend down"));

    const res = await logout(post("/api/auth/logout", {}, SESSION));

    expect(res.status).toBe(200);
    expect(cookieHeader(res, "access_token")).toContain("Max-Age=0");
  });

  it("does not call the backend when there is no session", async () => {
    const fetchMock = vi.spyOn(global, "fetch");

    const res = await logout(post("/api/auth/logout", {}));

    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /api/auth/set-password", () => {
  it("forwards token, type and password and sets no cookie", async () => {
    const fetchMock = backend(200, { success: true });

    const res = await setPassword(
      post("/api/auth/set-password", { token: "t-1", type: "invite", password: "a".repeat(12) }),
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend:8000/api/v1/auth/set-password");
    expect(JSON.parse(init!.body as string)).toEqual({
      token: "t-1",
      type: "invite",
      password: "a".repeat(12),
    });
    expect(await res.json()).toEqual({ success: true });
    expect(res.headers.getSetCookie()).toHaveLength(0);
  });

  it("accepts the legacy token_hash spelling so emailed links keep working", async () => {
    const fetchMock = backend(200, { success: true });

    await setPassword(
      post("/api/auth/set-password", { token_hash: "old-1", type: "recovery", password: "a".repeat(12) }),
    );

    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string).token).toBe("old-1");
  });

  it("passes the expired-link message through unchanged", async () => {
    backend(401, { detail: "This link is invalid or has expired. Ask the admin for a new one." });

    const res = await setPassword(
      post("/api/auth/set-password", { token: "t", type: "invite", password: "a".repeat(12) }),
    );

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({
      error: "This link is invalid or has expired. Ask the admin for a new one.",
    });
  });

  it("passes the password length rule through unchanged", async () => {
    backend(400, { detail: "Password must be 12–128 characters." });

    const res = await setPassword(
      post("/api/auth/set-password", { token: "t", type: "invite", password: "short" }),
    );

    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("Password must be 12–128 characters.");
  });
});
