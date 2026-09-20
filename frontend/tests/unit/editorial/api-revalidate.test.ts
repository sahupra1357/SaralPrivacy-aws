/** frontend/app/api/revalidate — kept: the backend busts ISR tags/paths through it. */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/cache", () => ({ revalidateTag: vi.fn(), revalidatePath: vi.fn() }));
import { revalidatePath, revalidateTag } from "next/cache";
import { GET, POST } from "@/app/api/revalidate/route";

const SECRET = "cron-secret";

function post(body: unknown, auth = `Bearer ${SECRET}`): NextRequest {
  return new NextRequest("http://localhost/api/revalidate", {
    method: "POST",
    headers: { authorization: auth, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("/api/revalidate", () => {
  beforeEach(() => {
    vi.stubEnv("CRON_SECRET", SECRET);
    vi.mocked(revalidateTag).mockReset();
    vi.mocked(revalidatePath).mockReset();
  });
  afterEach(() => vi.unstubAllEnvs());

  it("POST {tag} revalidates that tag", async () => {
    const res = await POST(post({ tag: "blog-posts" }));
    expect(res.status).toBe(200);
    expect(revalidateTag).toHaveBeenCalledWith("blog-posts", "default");
    expect(await res.json()).toMatchObject({ revalidated: true, tag: "blog-posts" });
  });

  it("POST {path} revalidates that path", async () => {
    const res = await POST(post({ path: "/blog/some-post" }));
    expect(res.status).toBe(200);
    expect(revalidatePath).toHaveBeenCalledWith("/blog/some-post");
  });

  it("POST {path, type: layout} refreshes every page under the path (start-up warm-up)", async () => {
    const res = await POST(post({ path: "/", type: "layout" }));
    expect(res.status).toBe(200);
    expect(revalidatePath).toHaveBeenCalledWith("/", "layout");
    expect(await res.json()).toMatchObject({ revalidated: true, path: "/", type: "layout" });
  });

  it("POST rejects a wrong secret", async () => {
    const res = await POST(post({ tag: "briefings" }, "Bearer wrong"));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Unauthorized." });
    expect(revalidateTag).not.toHaveBeenCalled();
  });

  it("POST without tag or path is a 400", async () => {
    const res = await POST(post({}));
    expect(res.status).toBe(400);
  });

  it("GET with the header secret clears the briefings tag (legacy form)", async () => {
    const req = new NextRequest("http://localhost/api/revalidate", { headers: { "x-revalidate-secret": SECRET } });
    const res = await GET(req);
    expect(res.status).toBe(200);
    expect(revalidateTag).toHaveBeenCalledWith("briefings", "default");
  });

  it("GET with ?secret= still works and rejects when unset", async () => {
    expect((await GET(new NextRequest(`http://localhost/api/revalidate?secret=${SECRET}`))).status).toBe(200);
    vi.stubEnv("CRON_SECRET", "");
    vi.stubEnv("BRIEFING_CRON_SECRET", "");
    expect((await GET(new NextRequest("http://localhost/api/revalidate?secret="))).status).toBe(401);
  });
});
