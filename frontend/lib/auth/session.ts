/**
 * session.ts — verify the backend's HS256 access token at the edge.
 *
 * Replaces the hand-rolled `v1.<payload>.<sig>` HMAC cookie from lib/adminSession.ts.
 * The token is now minted by FastAPI (`/api/v1/auth/mfa/verify`) and signed with the
 * same `SECRET_KEY` both processes read, so proxy.ts can decide whether to render
 * /admin without a round trip.
 *
 * Web Crypto (globalThis.crypto.subtle) on purpose, not node:crypto — this module is
 * imported by proxy.ts as well as Node route handlers, and Web Crypto is the API
 * available in both runtimes.
 *
 * What this check does NOT know: whether the session row was revoked. Revocation is
 * enforced by the backend on every API call (`deps.CurrentUser` re-reads app.sessions),
 * so the worst a revoked token buys is a rendered admin shell whose data calls all 401.
 */

export const ACCESS_TOKEN_COOKIE = "access_token";
export const ACCESS_TOKEN_MAX_AGE = 60 * 60 * 8; // 8 hours — matches ACCESS_TOKEN_EXPIRE_MINUTES

export const MFA_PENDING_COOKIE = "mfa_pending";
export const MFA_PENDING_MAX_AGE = 60 * 10; // 10 minutes — enough to scan a QR code
/** Scoped so the browser only ever sends it to the MFA routes. */
export const MFA_PENDING_COOKIE_PATH = "/api/auth/mfa";

export type AdminRole = "admin" | "blogger";

export interface AdminSession {
  role: AdminRole;
  name?: string;
  userId?: string;
  /** The token's `jti` — the app.sessions row id the backend can revoke. */
  sessionId?: string;
}

interface AccessClaims {
  sub?: unknown;
  role?: unknown;
  name?: unknown;
  jti?: unknown;
  purpose?: unknown;
  exp?: unknown;
}

const encoder = new TextEncoder();

/** Web Crypto takes a BufferSource; backing every array with its own ArrayBuffer keeps
 *  TypeScript's Uint8Array<ArrayBufferLike> out of the signatures below. */
function toBytes(text: string): Uint8Array<ArrayBuffer> {
  const src = encoder.encode(text);
  const out = new Uint8Array(new ArrayBuffer(src.length));
  out.set(src);
  return out;
}

function b64urlDecode(value: string): Uint8Array<ArrayBuffer> | null {
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    const bytes = new Uint8Array(new ArrayBuffer(bin.length));
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  } catch {
    return null;
  }
}

function getSecret(): string | null {
  return (process.env.SECRET_KEY || "").trim() || null;
}

/** Verified claims of an HS256 JWT, or null. Signature first, then exp, then shape. */
export async function verifyAccessToken(
  token: string | undefined | null
): Promise<AdminSession | null> {
  if (!token) return null;
  const secret = getSecret();
  if (!secret) return null; // no secret → nothing can be verified, so nothing is trusted

  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [headerB64, payloadB64, sigB64] = parts;

  const headerBytes = b64urlDecode(headerB64);
  if (!headerBytes) return null;
  let alg: unknown;
  try {
    alg = JSON.parse(new TextDecoder().decode(headerBytes))?.alg;
  } catch {
    return null;
  }
  // Pinned: an "alg":"none" or RS256 token must never be accepted here.
  if (alg !== "HS256") return null;

  const claimedSig = b64urlDecode(sigB64);
  if (!claimedSig) return null;
  const key = await crypto.subtle.importKey(
    "raw",
    toBytes(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );
  const ok = await crypto.subtle.verify(
    "HMAC",
    key,
    claimedSig,
    toBytes(`${headerB64}.${payloadB64}`)
  );
  if (!ok) return null;

  const payloadBytes = b64urlDecode(payloadB64);
  if (!payloadBytes) return null;
  let claims: AccessClaims;
  try {
    claims = JSON.parse(new TextDecoder().decode(payloadBytes));
  } catch {
    return null;
  }

  if (claims.purpose !== "access") return null; // a 10-minute MFA token is not a session
  if (claims.role !== "admin" && claims.role !== "blogger") return null;
  if (typeof claims.exp !== "number" || claims.exp < Math.floor(Date.now() / 1000)) return null;

  return {
    role: claims.role,
    ...(typeof claims.name === "string" && claims.name ? { name: claims.name } : {}),
    ...(typeof claims.sub === "string" && claims.sub ? { userId: claims.sub } : {}),
    ...(typeof claims.jti === "string" && claims.jti ? { sessionId: claims.jti } : {}),
  };
}

interface CookieReader {
  cookies: { get(name: string): { value: string } | undefined };
}

/** Convenience for route handlers and middleware holding a NextRequest. */
export async function getSession(request: CookieReader): Promise<AdminSession | null> {
  return verifyAccessToken(request.cookies.get(ACCESS_TOKEN_COOKIE)?.value);
}

/** Guard helper: returns the session only if its role is in `roles`. */
export async function requireRole(
  request: CookieReader,
  roles: AdminRole[]
): Promise<AdminSession | null> {
  const session = await getSession(request);
  if (!session || !roles.includes(session.role)) return null;
  return session;
}

/** Cookie options shared by the auth route handlers. `secure` only on https (mnsai rule). */
export function cookieOptions(request: { nextUrl: { protocol: string } }, path: string, maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: request.nextUrl.protocol === "https:",
    path,
    maxAge,
  };
}
