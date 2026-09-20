/**
 * Admin briefings + blog list pages now read and act through the backend proxy
 * (/api/proxy/api/v1/{briefings,blog}/...) instead of /api/admin/data and /api/briefings/*.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminBriefingsPage from "@/app/(backoffice)/admin/briefings/page";
import AdminBlogPage from "@/app/(backoffice)/admin/blog/page";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const NOW = new Date().toISOString();

describe("admin briefings page", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      if (url.startsWith("/api/proxy/api/v1/briefings/admin/all")) {
        return json({
          documents: [
            { $id: "b1", $createdAt: NOW, title: "Approved briefing", slug: "approved", status: "approved" },
            { $id: "b2", $createdAt: NOW, title: "Sent briefing", slug: "sent", status: "sent" },
          ],
          total: 2,
        });
      }
      if (url === "/api/proxy/api/v1/briefings/send") return json({ success: true, sent: 7, failed: 0, total: 7 });
      return json({ error: "unexpected" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", () => true);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("lists every briefing from the admin endpoint", async () => {
    render(<AdminBriefingsPage />);
    expect(await screen.findByText("Approved briefing")).toBeInTheDocument();
    expect(screen.getByText("Sent briefing")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/proxy/api/v1/briefings/admin/all?limit=100");
  });

  it("sends an approved briefing through the proxy and reports the count", async () => {
    render(<AdminBriefingsPage />);
    await screen.findByText("Approved briefing");
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    expect(await screen.findByText("✅ Sent to 7 subscribers.")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([u]) => u === "/api/proxy/api/v1/briefings/send");
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({ briefingId: "b1" });
  });

  it("shows the backend error when a send fails", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.includes("admin/all")
        ? json({ documents: [{ $id: "b1", $createdAt: NOW, title: "Approved briefing", slug: "a", status: "approved" }] })
        : json({ error: "Failed to send briefing." }, 500),
    );
    render(<AdminBriefingsPage />);
    await screen.findByText("Approved briefing");
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    expect(await screen.findByText("❌ Failed to send briefing.")).toBeInTheDocument();
  });

  it("Generate Now hits the backend generate endpoint and surfaces its 410", async () => {
    vi.stubGlobal("prompt", () => "secret");
    fetchMock.mockImplementation(async (url: string) =>
      url.includes("admin/all")
        ? json({ documents: [] })
        : json({ error: "Vercel cron disabled. Use n8n pipeline → POST /api/briefings/generate." }, 410),
    );
    render(<AdminBriefingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Generate Now/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/proxy/api/v1/briefings/generate",
        expect.objectContaining({ method: "GET" }),
      ),
    );
    expect(
      await screen.findByText("❌ Vercel cron disabled. Use n8n pipeline → POST /api/briefings/generate."),
    ).toBeInTheDocument();
  });
});

describe("admin blog list page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists posts from the blog admin endpoint with edit links", async () => {
    const fetchMock = vi.fn(async () =>
      json({
        documents: [
          {
            $id: "p1",
            $createdAt: NOW,
            title: "Draft post",
            slug: "draft-post",
            lane: "myth-fact",
            status: "draft",
            validation_score: 88,
            validated_at: "",
            author: "Desk",
            read_time: 3,
            featured: false,
          },
        ],
        total: 1,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminBlogPage />);
    expect(await screen.findByText("Draft post")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/proxy/api/v1/blog/admin/all?limit=100");
    expect(document.querySelector('a[href="/admin/blog/p1/edit"]')).not.toBeNull();
  });
});
