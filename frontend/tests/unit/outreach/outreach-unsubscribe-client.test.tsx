import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OutreachUnsubscribeClient from "@/app/[locale]/unsubscribe/outreach/OutreachUnsubscribeClient";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("OutreachUnsubscribeClient", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the token through the proxy and confirms removal", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true, already: false }));
    render(<OutreachUnsubscribeClient token="tok-u" />);

    expect(await screen.findByText("You’ve been removed.")).toBeInTheDocument();
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/outreach/unsubscribe");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ token: "tok-u" });
  });

  it("reports an address that was already removed", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true, already: true }));
    render(<OutreachUnsubscribeClient token="tok-u" />);

    expect(await screen.findByText("Already removed.")).toBeInTheDocument();
  });

  it("shows the backend's error detail", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ detail: "Link not recognised." }, 404));
    render(<OutreachUnsubscribeClient token="nope" />);

    expect(await screen.findByText("Link not recognised.")).toBeInTheDocument();
  });

  it("does not call the backend without a token", async () => {
    const fetchSpy = vi.spyOn(global, "fetch");
    render(<OutreachUnsubscribeClient token="" />);

    expect(screen.getByText("Invalid link")).toBeInTheDocument();
    await waitFor(() => expect(fetchSpy).not.toHaveBeenCalled());
  });
});
