import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
import { ApiError, apiGet, apiUrl } from "@/lib/api";

describe("lib/api", () => {
  it("builds backend URLs under /api/v1", () => {
    expect(apiUrl("/editorial/blog")).toBe("http://localhost:8000/api/v1/editorial/blog");
    expect(apiUrl("editorial/blog")).toBe("http://localhost:8000/api/v1/editorial/blog");
  });

  it("parses JSON and forwards cache tags", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response('[{"id":1}]', { status: 200 }));
    const data = await apiGet<{ id: number }[]>("/x", { tags: ["t"], revalidate: 60 });
    expect(data).toEqual([{ id: 1 }]);
    expect((fetchMock.mock.calls[0][1] as { next: unknown }).next).toEqual({ tags: ["t"], revalidate: 60 });
  });

  it("throws ApiError with status and body on failure", async () => {
    // A Response body can be read once, so hand each call a fresh one.
    vi.spyOn(global, "fetch").mockImplementation(async () => new Response('{"detail":"nope"}', { status: 404 }));
    await expect(apiGet("/missing")).rejects.toMatchObject({ status: 404, body: { detail: "nope" } });
    await expect(apiGet("/missing")).rejects.toBeInstanceOf(ApiError);
  });
});
