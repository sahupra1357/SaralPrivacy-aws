import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ContactContent from "@/app/[locale]/contact/ContactContent";

vi.mock("@/lib/analytics", () => ({ trackEvent: { consultationRequest: vi.fn() } }));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function fillRequiredFields() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
  await user.type(screen.getByLabelText(/work email/i), "asha@example.com");
  await user.type(screen.getByLabelText(/mobile number/i), "+91 98765 43210");
  await user.type(screen.getByLabelText(/company name/i), "Rao Textiles");
  await user.selectOptions(screen.getByLabelText(/^industry/i), "recruitment");
  await user.type(
    screen.getByLabelText(/briefly describe your situation/i),
    "We need a privacy notice.",
  );
  await user.click(screen.getByRole("checkbox"));
  return user;
}

describe("ContactContent", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ success: true, message: "Your consultation request has been received." }),
    );
  });

  it("posts the consultation request through the backend proxy", async () => {
    render(<ContactContent />);

    const user = await fillRequiredFields();
    await user.click(screen.getByRole("button", { name: /submit consultation request/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/contact");
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body).toMatchObject({
      fullName: "Asha Rao",
      workEmail: "asha@example.com",
      companyName: "Rao Textiles",
      issueSummary: "We need a privacy notice.",
      consentContact: true,
      hp_url: "",
    });
  });

  it("shows the confirmation state after a successful submission", async () => {
    render(<ContactContent />);

    const user = await fillRequiredFields();
    await user.click(screen.getByRole("button", { name: /submit consultation request/i }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /submit consultation request/i })).toBeNull(),
    );
  });

  it("keeps submission blocked until consent is given", async () => {
    const user = userEvent.setup();
    render(<ContactContent />);

    await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
    await user.click(screen.getByRole("button", { name: /submit consultation request/i }));

    expect(global.fetch).not.toHaveBeenCalled();
  });
});
