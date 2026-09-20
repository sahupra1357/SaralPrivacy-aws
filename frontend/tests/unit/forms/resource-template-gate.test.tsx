import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ResourceTemplateGate from "@/components/ResourceTemplateGate";

const TEMPLATES = [
  {
    title: "Data Inventory Register",
    file: "data-inventory-register.xlsx",
    tag: "XLSX",
    desc: "Track what you hold.",
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
  await user.click(screen.getByRole("button", { name: /data inventory register/i }));
  await user.type(screen.getByPlaceholderText("you@company.com"), "meera@example.com");
  await user.type(screen.getByPlaceholderText("e.g. Priya Sharma"), "Meera");
  await user.type(screen.getByPlaceholderText("e.g. Acme Solutions Pvt Ltd"), "Sunrise Salon");
  await user.selectOptions(screen.getByRole("combobox"), "1–10 employees");
  await user.type(screen.getByPlaceholderText("e.g. +91 98765 43210"), "+919812345678");
  return user;
}

describe("ResourceTemplateGate", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("posts the gate lead through the backend proxy", async () => {
    render(<ResourceTemplateGate templates={TEMPLATES} />);

    const user = await openGateAndFill();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/template-download");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      email: "meera@example.com",
      contactName: "Meera",
      businessName: "Sunrise Salon",
      employees: "1–10 employees",
      phone: "+919812345678",
      consentContact: false,
      consentBriefings: false,
      templateName: "Data Inventory Register",
      reportToken: "",
      source: "resources_page",
    });
  });

  it("remembers the contact and unlocks instant downloads on success", async () => {
    render(<ResourceTemplateGate templates={TEMPLATES} />);

    const user = await openGateAndFill();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    await waitFor(() => expect(localStorage.getItem("sp_rg_v1")).toBe("1"));
    expect(JSON.parse(localStorage.getItem("saral_template_contact") ?? "{}")).toMatchObject({
      email: "meera@example.com",
      contactPersonName: "Meera",
      businessName: "Sunrise Salon",
    });
  });

  it("refuses to submit with required fields missing", async () => {
    const user = userEvent.setup();
    render(<ResourceTemplateGate templates={TEMPLATES} />);

    await user.click(screen.getByRole("button", { name: /data inventory register/i }));
    // The inputs carry `required`, so a real click is stopped by native validation before
    // the app's own check runs. Submit the form directly to exercise that check.
    const submit = screen.getByRole("button", { name: /download template/i });
    fireEvent.submit(submit.closest("form")!);

    expect(global.fetch).not.toHaveBeenCalled();
    expect(await screen.findByText("Please fill in all required fields.")).toBeInTheDocument();
  });
});
