import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MagicSubscribeClient from "@/app/[locale]/subscribe/MagicSubscribeClient";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("MagicSubscribeClient", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the magic token through the proxy", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true, already: false, name: "Asha Rao" }));
    render(<MagicSubscribeClient token="tok-1" />);

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/outreach/subscribe");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ token: "tok-1" });
  });

  it("welcomes the new subscriber by first name", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true, already: false, name: "Asha Rao" }));
    render(<MagicSubscribeClient token="tok-1" />);

    expect(await screen.findByText("You’re in, Asha!")).toBeInTheDocument();
  });

  it("says so when already subscribed", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true, already: true, name: "" }));
    render(<MagicSubscribeClient token="tok-1" />);

    expect(await screen.findByText("You’re already subscribed!")).toBeInTheDocument();
  });

  it("shows the backend's error detail for an unknown link", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Link not recognised or already used." }, 404),
    );
    render(<MagicSubscribeClient token="nope" />);

    expect(await screen.findByText("Link not recognised or already used.")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("shows a network error when the request fails", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("offline"));
    render(<MagicSubscribeClient token="tok-1" />);

    expect(await screen.findByText("Network error. Please try again.")).toBeInTheDocument();
  });
});
