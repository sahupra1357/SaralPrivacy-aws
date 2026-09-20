import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace, refresh: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/unsubscribe",
  useSearchParams: () => search,
}));

import UnsubscribePage from "@/app/[locale]/unsubscribe/page";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("/unsubscribe", () => {
  beforeEach(() => {
    replace.mockClear();
    search = new URLSearchParams({ email: "leela@example.com", sig: "abc123" });
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true }));
  });

  it("posts the signed one-click unsubscribe through the proxy", async () => {
    render(<UnsubscribePage />);

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/subscribers/unsubscribe");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      email: "leela@example.com",
      sig: "abc123",
    });
  });

  it("confirms once the backend has removed the address", async () => {
    render(<UnsubscribePage />);

    expect(await screen.findByText("You've been unsubscribed")).toBeInTheDocument();
  });

  it("shows the manual-removal fallback when the request fails", async () => {
    vi.mocked(global.fetch).mockRejectedValue(new Error("offline"));
    render(<UnsubscribePage />);

    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
  });

  it("redirects to the consent hub when there is no email in the link", async () => {
    search = new URLSearchParams();
    render(<UnsubscribePage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/consent-preferences"));
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
