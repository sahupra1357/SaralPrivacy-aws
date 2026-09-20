import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { proxyRequest } from "@/app/api/proxy/[...path]/route";

function req(url: string, init: RequestInit & { cookie?: string } = {}) {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("cookie", init.cookie);
  return new NextRequest(new URL(url, "http://localhost:3000"), { ...init, headers });
}

describe("proxy route", () => {
  it("forwards path, query and bearer from the access_token cookie", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response('{"ok":true}', { status: 200, headers: { "content-type": "application/json" } }),
    );
    const res = await proxyRequest(
      req("/api/proxy/api/v1/forms/x?a=1", { cookie: "access_token=abc.def.ghi" }),
      ["api", "v1", "forms", "x"],
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/forms/x?a=1");
    expect((init!.headers as Headers).get("authorization")).toBe("Bearer abc.def.ghi");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/json");
  });

  it("sends no authorization header without a cookie and passes JSON bodies", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response("", { status: 201 }));
    await proxyRequest(
      req("/api/proxy/api/v1/forms/contact", {
        method: "POST",
        body: '{"name":"A"}',
        headers: { "content-type": "application/json" },
      }),
      ["api", "v1", "forms", "contact"],
    );
    const init = fetchMock.mock.calls[0][1]!;
    expect((init.headers as Headers).get("authorization")).toBeNull();
    expect(init.body).toBe('{"name":"A"}');
    expect(init.method).toBe("POST");
  });

  it("passes through retry-after on 429", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(new Response("", { status: 429, headers: { "retry-after": "30" } }));
    const res = await proxyRequest(req("/api/proxy/api/v1/x"), ["api", "v1", "x"]);
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("30");
  });
});
