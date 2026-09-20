/**
 * proxy.ts — the /admin gate only. Locale routing is next-intl's and is stubbed here so
 * the table stays about auth: who reaches /admin, who is bounced to /admin/login, and
 * which paths a blogger may open.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";

vi.mock("next-intl/middleware", () => ({
  default: () => () => NextResponse.next({ headers: { "x-intl": "handled" } }),
}));

import { proxy } from "@/proxy";

const SECRET = "unit-test-secret";
const encoder = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function mint(role: string, { secret = SECRET, expired = false } = {}): Promise<string> {
  const header = b64url(encoder.encode(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const payload = b64url(
    encoder.encode(
      JSON.stringify({
        sub: "u-1",
        role,
        jti: "s-1",
        purpose: "access",
        exp: Math.floor(Date.now() / 1000) + (expired ? -60 : 3600),
      }),
    ),
  );
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(`${header}.${payload}`));
  return `${header}.${payload}.${b64url(new Uint8Array(sig))}`;
}

function request(path: string, token?: string) {
  const headers = new Headers();
  if (token) headers.set("cookie", `access_token=${token}`);
  return new NextRequest(new URL(`http://localhost:3000${path}`), { headers });
}

function redirectedTo(res: Response): string | null {
  return res.headers.get("location");
}

describe("proxy /admin gate", () => {
  beforeEach(() => {
    process.env.SECRET_KEY = SECRET;
  });

  it("sends an unauthenticated visitor to /admin/login", async () => {
    const res = await proxy(request("/admin"));
    expect(redirectedTo(res)).toBe("http://localhost:3000/admin/login");
  });

  it("sends a tampered token to /admin/login", async () => {
    const token = (await mint("admin")).slice(0, -2) + "xx";
    expect(redirectedTo(await proxy(request("/admin", token)))).toBe(
      "http://localhost:3000/admin/login",
    );
  });

  it("sends an expired token to /admin/login", async () => {
    const token = await mint("admin", { expired: true });
    expect(redirectedTo(await proxy(request("/admin", token)))).toBe(
      "http://localhost:3000/admin/login",
    );
  });

  it("sends a token signed with the wrong secret to /admin/login", async () => {
    const token = await mint("admin", { secret: "other" });
    expect(redirectedTo(await proxy(request("/admin", token)))).toBe(
      "http://localhost:3000/admin/login",
    );
  });

  it("lets an admin through to any admin route", async () => {
    const token = await mint("admin");
    for (const path of ["/admin", "/admin/blog", "/admin/leads", "/admin/seo"]) {
      expect(redirectedTo(await proxy(request(path, token)))).toBeNull();
    }
  });

  it("keeps a blogger inside the blog editor", async () => {
    const token = await mint("blogger");
    expect(redirectedTo(await proxy(request("/admin/blog", token)))).toBeNull();
    expect(redirectedTo(await proxy(request("/admin/blog/abc/edit", token)))).toBeNull();
    expect(redirectedTo(await proxy(request("/admin/leads", token)))).toBe(
      "http://localhost:3000/admin/blog",
    );
    expect(redirectedTo(await proxy(request("/admin", token)))).toBe(
      "http://localhost:3000/admin/blog",
    );
  });

  it("leaves the public admin pages open", async () => {
    expect(redirectedTo(await proxy(request("/admin/login")))).toBeNull();
    expect(redirectedTo(await proxy(request("/admin/set-password")))).toBeNull();
  });

  it("308s a locale-prefixed admin path to the unprefixed one before gating", async () => {
    const res = await proxy(request("/hi/admin/leads"));
    expect(res.status).toBe(308);
    expect(redirectedTo(res)).toBe("http://localhost:3000/admin/leads");
  });
});
