import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WhitePaperContent from "@/app/[locale]/white-paper/WhitePaperContent";

vi.mock("@/lib/analytics", () => ({ trackEvent: { download: vi.fn() } }));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function firstRealOption(select: HTMLSelectElement): string {
  return Array.from(select.options).find((o) => o.value !== "")!.value;
}

async function fillGate() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/full name/i), "Neha Gupta");
  await user.type(screen.getByLabelText(/work email/i), "neha@example.com");
  await user.type(screen.getByLabelText(/company name/i), "Gupta Foods");
  const industry = screen.getByLabelText(/industry/i) as HTMLSelectElement;
  await user.selectOptions(industry, firstRealOption(industry));
  await user.selectOptions(screen.getByLabelText(/company size/i), "11-50");
  return user;
}

describe("WhitePaperContent", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({
        success: true,
        downloadUrl: "https://cdn.example/dpdpa-guide-en.pdf",
        language: "en",
        partial: false,
        message: "Download ready.",
      }),
    );
  });

  it("posts the guide request through the backend proxy", async () => {
    render(<WhitePaperContent />);

    const user = await fillGate();
    await user.click(screen.getByRole("button", { name: /download the guide/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/white-paper");
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body).toMatchObject({
      fullName: "Neha Gupta",
      workEmail: "neha@example.com",
      companyName: "Gupta Foods",
      companySize: "11-50",
    });
    expect(body.industry).toBeTruthy();
  });

  it("validates required fields before calling the backend", async () => {
    const user = userEvent.setup();
    render(<WhitePaperContent />);

    await user.click(screen.getByRole("button", { name: /download the guide/i }));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(await screen.findByText("Please select your industry")).toBeInTheDocument();
  });

  it("shows the backend error message when the request is rejected", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ detail: "Too many requests. Please try again later." }, 429),
    );
    render(<WhitePaperContent />);

    const user = await fillGate();
    await user.click(screen.getByRole("button", { name: /download the guide/i }));

    expect(
      await screen.findByText("Too many requests. Please try again later."),
    ).toBeInTheDocument();
  });
});
