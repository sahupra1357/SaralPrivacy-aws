import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Radix's Select renders through a portal and needs pointer APIs jsdom lacks, so the
// presentational primitive is swapped for a native <select>. Everything under test —
// the zod schema, the fetch target and the success/error handling — stays real.
vi.mock("@/components/ui/select", () => {
  const items: { value: string; label: ReactNode }[] = [];
  return {
    Select: ({
      value,
      onValueChange,
      children,
    }: {
      value?: string;
      onValueChange: (v: string) => void;
      children: ReactNode;
    }) => (
      <>
        {children}
        <select
          aria-label="Select Template"
          value={value ?? ""}
          onChange={(e) => onValueChange(e.target.value)}
        >
          <option value="" />
          {items.map((i) => (
            <option key={i.value} value={i.value}>
              {String(i.label)}
            </option>
          ))}
        </select>
      </>
    ),
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => {
      if (!items.some((i) => i.value === value)) items.push({ value, label: children });
      return null;
    },
    SelectGroup: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectLabel: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectSeparator: () => null,
  };
});

import { TemplateDownloadForm } from "@/components/TemplateDownloadForm";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function fillForm() {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("you@company.com"), "ravi@example.com");
  await user.type(screen.getByPlaceholderText("Ravi Sharma"), "Ravi Kumar");
  await user.type(screen.getByPlaceholderText("Acme Pvt Ltd"), "Kumar Legal");
  await user.selectOptions(screen.getByLabelText("Select Template"), "privacy-notice");
  const phone = screen.getByPlaceholderText("+91 98765 43210");
  await user.clear(phone);
  await user.type(phone, "+919876543210");
  return user;
}

describe("TemplateDownloadForm", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({
        success: true,
        message: "Template sent successfully",
        downloadUrl: "https://saralprivacy.com/templates/privacy-notice.docx",
        email: true,
        whatsapp: false,
      }),
    );
  });

  it("posts the template request through the backend proxy", async () => {
    render(<TemplateDownloadForm />);

    const user = await fillForm();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/templates/download");
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({
      email: "ravi@example.com",
      contactPersonName: "Ravi Kumar",
      businessName: "Kumar Legal",
      templateSelected: "privacy-notice",
      phoneNumber: "+919876543210",
      consentContact: false,
      consentBriefings: false,
    });
  });

  it("confirms delivery and remembers the contact", async () => {
    render(<TemplateDownloadForm />);

    const user = await fillForm();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    expect(await screen.findByText("Template sent!")).toBeInTheDocument();
    expect(localStorage.getItem("sp_rg_v1")).toBe("1");
    expect(JSON.parse(localStorage.getItem("saral_template_contact") ?? "{}")).toMatchObject({
      email: "ravi@example.com",
      contactPersonName: "Ravi Kumar",
    });
  });

  it("blocks an invalid Indian phone number before reaching the backend", async () => {
    const user = userEvent.setup();
    render(<TemplateDownloadForm />);

    await user.type(screen.getByPlaceholderText("you@company.com"), "ravi@example.com");
    await user.type(screen.getByPlaceholderText("Ravi Sharma"), "Ravi Kumar");
    await user.type(screen.getByPlaceholderText("Acme Pvt Ltd"), "Kumar Legal");
    await user.selectOptions(screen.getByLabelText("Select Template"), "privacy-notice");
    await user.type(screen.getByPlaceholderText("+91 98765 43210"), "12345");
    await user.click(screen.getByRole("button", { name: /download template/i }));

    expect(
      await screen.findByText("Please enter a valid Indian phone number (+91 format)"),
    ).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("surfaces the backend message when delivery fails", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ message: "Failed to send template email. Please try again." }, 500),
    );
    render(<TemplateDownloadForm />);

    const user = await fillForm();
    await user.click(screen.getByRole("button", { name: /download template/i }));

    expect(
      await screen.findByText("Failed to send template email. Please try again."),
    ).toBeInTheDocument();
  });
});
