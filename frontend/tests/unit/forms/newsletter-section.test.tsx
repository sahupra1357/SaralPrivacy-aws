import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { NewsletterSection } from "@/components/home/NewsletterSection";

vi.mock("@/lib/analytics", () => ({ trackEvent: { subscribe: vi.fn() } }));

function renderSection() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <NewsletterSection />
    </NextIntlClientProvider>,
  );
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("NewsletterSection", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ success: true, message: "Subscription successful." }),
    );
  });

  it("posts the subscription through the backend proxy", async () => {
    const user = userEvent.setup();
    renderSection();

    await user.type(screen.getByLabelText(/work email/i), "priya@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /subscribe to briefings/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/subscribe");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: "",
      email: "priya@example.com",
      frequency: "daily",
      consentEmail: true,
    });
  });

  it("does not submit until consent is given", async () => {
    const user = userEvent.setup();
    renderSection();

    await user.type(screen.getByLabelText(/work email/i), "priya@example.com");
    await user.click(screen.getByRole("button", { name: /subscribe to briefings/i }));

    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("shows the backend's message when the request is rejected", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ detail: "Invalid email address." }, 400),
    );
    const user = userEvent.setup();
    renderSection();

    await user.type(screen.getByLabelText(/work email/i), "nope@example");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /subscribe to briefings/i }));

    expect(await screen.findByText("Invalid email address.")).toBeInTheDocument();
  });
});
