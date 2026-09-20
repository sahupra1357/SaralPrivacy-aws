import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BriefingSubscribeCard } from "@/components/briefings/BriefingSubscribeCard";

vi.mock("@/lib/analytics", () => ({ trackEvent: { subscribe: vi.fn() } }));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function fillAndSubmit() {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("Your name"), "Ravi");
  await user.type(screen.getByPlaceholderText("your@email.com"), "ravi@example.com");
  await user.click(screen.getByRole("button"));
}

describe("BriefingSubscribeCard", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true }));
  });

  it("posts to the forms subscribe route through the proxy", async () => {
    render(<BriefingSubscribeCard />);

    await fillAndSubmit();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/subscribe");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: "Ravi",
      email: "ravi@example.com",
      frequency: "daily",
      consentEmail: true,
    });
  });

  it("confirms the subscription on success", async () => {
    render(<BriefingSubscribeCard />);

    await fillAndSubmit();

    expect(await screen.findByText("You're subscribed!")).toBeInTheDocument();
  });

  it("surfaces the backend error message", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ detail: "Too many requests. Please wait a moment and try again." }, 429),
    );
    render(<BriefingSubscribeCard />);

    await fillAndSubmit();

    expect(
      await screen.findByText("Too many requests. Please wait a moment and try again."),
    ).toBeInTheDocument();
  });
});
