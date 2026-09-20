/** Server-side helpers: bearer forwarding, sidebar Sign Out, SEO "Mark requested". */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const cookieJar = new Map<string, string>();
const store = {
  get: (name: string) => (cookieJar.has(name) ? { name, value: cookieJar.get(name)! } : undefined),
  delete: vi.fn((name: string) => cookieJar.delete(name)),
};
vi.mock("next/headers", () => ({ cookies: async () => store }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("@/lib/auth/session", () => ({
  ACCESS_TOKEN_COOKIE: "access_token",
  verifyAccessToken: vi.fn(),
}));

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { apiGet, apiPost } from "@/lib/api";
import { verifyAccessToken } from "@/lib/auth/session";
import { adminGet, adminPost, authHeaders } from "@/app/(backoffice)/admin/_lib/backend";
import { signOut } from "@/app/(backoffice)/admin/_lib/signOut";
import { markRequested } from "@/app/(backoffice)/admin/seo/actions";

beforeEach(() => {
  cookieJar.clear();
  vi.mocked(apiGet).mockReset();
  vi.mocked(apiPost).mockReset();
});

describe("_lib/backend", () => {
  it("forwards the access token as a bearer header and never caches", async () => {
    cookieJar.set("access_token", "jwt-1");
    vi.mocked(apiGet).mockResolvedValue({ ok: 1 });

    await adminGet("/admin/seo");
    await adminPost("/admin/seo/index-requests", { url: "u" });

    expect(apiGet).toHaveBeenCalledWith("/admin/seo", { headers: { authorization: "Bearer jwt-1" }, revalidate: 0 });
    expect(apiPost).toHaveBeenCalledWith("/admin/seo/index-requests", { url: "u" }, { headers: { authorization: "Bearer jwt-1" } });
  });

  it("sends no header without a cookie", async () => {
    expect(await authHeaders()).toEqual({});
  });
});

describe("signOut", () => {
  it("revokes the backend session, drops the cookie and goes to the login page", async () => {
    cookieJar.set("access_token", "jwt-2");
    vi.mocked(apiPost).mockResolvedValue({ success: true });

    await signOut();

    expect(apiPost).toHaveBeenCalledWith("/auth/logout", {}, { headers: { authorization: "Bearer jwt-2" } });
    expect(store.delete).toHaveBeenCalledWith("access_token");
    expect(redirect).toHaveBeenCalledWith("/admin/login");
  });

  it("still signs out when the backend call fails", async () => {
    cookieJar.set("access_token", "jwt-3");
    vi.mocked(apiPost).mockRejectedValue(new Error("down"));
    await signOut();
    expect(cookieJar.has("access_token")).toBe(false);
    expect(redirect).toHaveBeenCalledWith("/admin/login");
  });
});

describe("markRequested", () => {
  function form(url: string) {
    const f = new FormData();
    f.set("url", url);
    return f;
  }

  it("records the URL in the backend ledger and refreshes the page", async () => {
    cookieJar.set("access_token", "jwt-4");
    vi.mocked(verifyAccessToken).mockResolvedValue({ role: "admin" });

    await markRequested(form(" https://saralprivacy.com/learn/consent "));

    expect(apiPost).toHaveBeenCalledWith(
      "/admin/seo/index-requests",
      { url: "https://saralprivacy.com/learn/consent" },
      { headers: { authorization: "Bearer jwt-4" } },
    );
    expect(revalidatePath).toHaveBeenCalledWith("/admin/seo");
  });

  it("refuses non-admins and other hosts", async () => {
    vi.mocked(verifyAccessToken).mockResolvedValue({ role: "blogger" });
    await expect(markRequested(form("https://saralprivacy.com/a"))).rejects.toThrow("Unauthorized");

    vi.mocked(verifyAccessToken).mockResolvedValue({ role: "admin" });
    await expect(markRequested(form("https://evil.example/"))).rejects.toThrow("URL must be on https://saralprivacy.com/");
    expect(apiPost).not.toHaveBeenCalled();
  });
});
