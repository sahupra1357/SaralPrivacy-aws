import { beforeEach, describe, expect, it } from "vitest";

import {
  ACCESS_TOKEN_COOKIE,
  ACCESS_TOKEN_MAX_AGE,
  MFA_PENDING_COOKIE_PATH,
  requireRole,
  verifyAccessToken,
} from "@/lib/auth/session";

const SECRET = "unit-test-secret";
const encoder = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Mint an HS256 JWT exactly as the FastAPI backend does (pyjwt, same claim names). */
async function mint(
  claims: Record<string, unknown>,
  { secret = SECRET, alg = "HS256" } = {}
): Promise<string> {
  const header = b64url(encoder.encode(JSON.stringify({ alg, typ: "JWT" })));
  const payload = b64url(encoder.encode(JSON.stringify(claims)));
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

const future = () => Math.floor(Date.now() / 1000) + 3600;

const validClaims = {
  sub: "11111111-1111-1111-1111-111111111111",
  role: "admin",
  name: "Desk",
  jti: "22222222-2222-2222-2222-222222222222",
  purpose: "access",
  exp: future(),
};

describe("verifyAccessToken", () => {
  beforeEach(() => {
    process.env.SECRET_KEY = SECRET;
  });

  it("accepts a well-formed token and returns role, name, user id and session id", async () => {
    const session = await verifyAccessToken(await mint(validClaims));
    expect(session).toEqual({
      role: "admin",
      name: "Desk",
      userId: validClaims.sub,
      sessionId: validClaims.jti,
    });
  });

  it("rejects a tampered signature", async () => {
    const token = await mint(validClaims);
    const [h, p, s] = token.split(".");
    expect(await verifyAccessToken(`${h}.${p}.${s.slice(0, -2)}xx`)).toBeNull();
  });

  it("rejects a token signed with a different secret (rotation kills sessions)", async () => {
    const token = await mint(validClaims, { secret: "rotated" });
    expect(await verifyAccessToken(token)).toBeNull();
  });

  it("rejects an expired token", async () => {
    const token = await mint({ ...validClaims, exp: Math.floor(Date.now() / 1000) - 1 });
    expect(await verifyAccessToken(token)).toBeNull();
  });

  it("rejects an unknown role", async () => {
    expect(await verifyAccessToken(await mint({ ...validClaims, role: "superuser" }))).toBeNull();
    expect(await verifyAccessToken(await mint({ ...validClaims, role: undefined }))).toBeNull();
  });

  it("rejects a pending MFA token — it is not a session", async () => {
    const token = await mint({ sub: validClaims.sub, purpose: "mfa", role: "admin", exp: future() });
    expect(await verifyAccessToken(token)).toBeNull();
  });

  it("rejects alg:none and garbage", async () => {
    expect(await verifyAccessToken(await mint(validClaims, { alg: "none" }))).toBeNull();
    expect(await verifyAccessToken("not.a.jwt")).toBeNull();
    expect(await verifyAccessToken("")).toBeNull();
    expect(await verifyAccessToken(undefined)).toBeNull();
  });

  it("verifies nothing when SECRET_KEY is unset", async () => {
    const token = await mint(validClaims);
    delete process.env.SECRET_KEY;
    expect(await verifyAccessToken(token)).toBeNull();
  });
});

describe("requireRole", () => {
  beforeEach(() => {
    process.env.SECRET_KEY = SECRET;
  });

  function reader(token?: string) {
    return { cookies: { get: (n: string) => (n === ACCESS_TOKEN_COOKIE && token ? { value: token } : undefined) } };
  }

  it("returns the session when the role is allowed", async () => {
    const token = await mint({ ...validClaims, role: "blogger" });
    expect(await requireRole(reader(token), ["admin", "blogger"])).toMatchObject({ role: "blogger" });
  });

  it("returns null for a role that is not listed", async () => {
    const token = await mint({ ...validClaims, role: "blogger" });
    expect(await requireRole(reader(token), ["admin"])).toBeNull();
  });

  it("returns null with no cookie at all", async () => {
    expect(await requireRole(reader(), ["admin"])).toBeNull();
  });
});

describe("cookie constants", () => {
  it("keep the 8-hour session and the MFA path scope", () => {
    expect(ACCESS_TOKEN_MAX_AGE).toBe(60 * 60 * 8);
    expect(MFA_PENDING_COOKIE_PATH).toBe("/api/auth/mfa");
  });
});
