import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TemplateGateModal from "@/components/TemplateGateModal";

const TEMPLATES = [
  {
    title: "Privacy Notice Template",
    file: "/templates/privacy-notice.docx",
    tag: "DOCX",
    desc: "A notice you can publish.",
  },
];

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function openGateAndFill() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /privacy notice template/i }));
  await user.type(screen.getByPlaceholderText("e.g. Acme Solutions Pvt Ltd"), "Sunrise Salon");
  await user.selectOptions(screen.getByRole("combobox"), "1–10 employees");
  await user.type(screen.getByPlaceholderText("e.g. Priya Sharma"), "Meera");
  await user.type(screen.getByPlaceholderText("e.g. +91 98765 43210"), "+919812345678");
  return user;
}

describe("TemplateGateModal", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("posts the gate lead through the backend proxy", async () => {
    render(<TemplateGateModal templates={TEMPLATES} reportToken="tok-1" email="meera@example.com" />);

    const user = await openGateAndFill();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/template-download");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      businessName: "Sunrise Salon",
      employees: "1–10 employees",
      contactName: "Meera",
      phone: "+919812345678",
      consentContact: false,
      consentBriefings: false,
      templateName: "Privacy Notice Template",
      reportToken: "tok-1",
      email: "meera@example.com",
    });
  });

  it("refuses to submit with required fields missing", async () => {
    const user = userEvent.setup();
    render(<TemplateGateModal templates={TEMPLATES} reportToken="tok-1" />);

    await user.click(screen.getByRole("button", { name: /privacy notice template/i }));
    // The inputs carry `required`, so a real click is stopped by native validation before
    // the app's own check runs. Submit the form directly to exercise that check.
    const submit = screen.getByRole("button", { name: /download template/i });
    fireEvent.submit(submit.closest("form")!);

    expect(global.fetch).not.toHaveBeenCalled();
    expect(await screen.findByText("Please fill in all required fields.")).toBeInTheDocument();
  });

  it("shows the backend error message", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ detail: "Required fields missing." }, 400),
    );
    render(<TemplateGateModal templates={TEMPLATES} reportToken="tok-1" />);

    const user = await openGateAndFill();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    expect(await screen.findByText("Required fields missing.")).toBeInTheDocument();
  });
});
