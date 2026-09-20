/**
 * admin/blog/[id]/edit reads the post from the backend with the editor's own bearer
 * token (drafts are editor-only) and unpacks sections_json for the editor.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "access_token" ? { value: "jwt-token" } : undefined) }),
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));
vi.mock("@/lib/auth/session", () => ({
  ACCESS_TOKEN_COOKIE: "access_token",
  verifyAccessToken: vi.fn(async () => ({ role: "blogger" })),
}));
vi.mock("@/lib/api", () => ({ apiGet: vi.fn() }));
vi.mock("@/components/admin/BlogEditor", () => ({
  default: (props: { docId: string; role: string; initialStatus: string; initialData: Record<string, unknown> }) => (
    <div>
      <span>editor:{props.docId}</span>
      <span>role:{props.role}</span>
      <span>status:{props.initialStatus}</span>
      <span>do-now:{String(props.initialData.section_do_now)}</span>
    </div>
  ),
}));

import { apiGet } from "@/lib/api";
import EditBlogPostPage from "@/app/(backoffice)/admin/blog/[id]/edit/page";

describe("admin blog edit page", () => {
  beforeEach(() => vi.mocked(apiGet).mockReset());

  it("loads the post with the session bearer and unpacks sections_json", async () => {
    vi.mocked(apiGet).mockResolvedValue({
      title: "T",
      slug: "t",
      status: "review",
      sections_json: JSON.stringify({ section_do_now: "Do this", primary_sources: "[]" }),
    });

    render(await EditBlogPostPage({ params: Promise.resolve({ id: "post-1" }) }));

    expect(apiGet).toHaveBeenCalledWith("/blog/post-1", {
      revalidate: 0,
      headers: { authorization: "Bearer jwt-token" },
    });
    expect(screen.getByText("editor:post-1")).toBeInTheDocument();
    expect(screen.getByText("role:blogger")).toBeInTheDocument();
    expect(screen.getByText("status:review")).toBeInTheDocument();
    expect(screen.getByText("do-now:Do this")).toBeInTheDocument();
  });

  it("404s when the backend has no such post", async () => {
    vi.mocked(apiGet).mockRejectedValue(new Error("API 404"));
    await expect(EditBlogPostPage({ params: Promise.resolve({ id: "missing" }) })).rejects.toThrow("NEXT_NOT_FOUND");
  });
});
