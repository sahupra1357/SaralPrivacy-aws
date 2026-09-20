import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ConsentPreferencesContent from "@/app/[locale]/consent-preferences/ConsentPreferencesContent";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function submitEmail(value: string) {
  const user = userEvent.setup();
  const input = document.querySelector("input[type='email']") as HTMLInputElement;
  await user.type(input, value);
  await user.click(screen.getByRole("button", { name: /unsubscribe/i }));
}

describe("ConsentPreferencesContent", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true }));
  });

  it("withdraws consent through the backend proxy", async () => {
    render(<ConsentPreferencesContent />);

    await submitEmail("leela@example.com");

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/subscribers/unsubscribe");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ email: "leela@example.com" });
  });

  it("confirms the withdrawal", async () => {
    render(<ConsentPreferencesContent />);

    await submitEmail("leela@example.com");

    expect(await screen.findByText("You've been unsubscribed")).toBeInTheDocument();
  });

  it("shows the error state when the backend refuses", async () => {
    vi.mocked(global.fetch).mockResolvedValue(jsonResponse({ detail: "email required" }, 400));
    render(<ConsentPreferencesContent />);

    await submitEmail("leela@example.com");

    await waitFor(() => expect(screen.queryByText("You've been unsubscribed")).toBeNull());
  });
});
