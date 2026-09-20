/**
 * The Notice Pack Builder's two network call sites after the migration: the export-gate
 * lead capture and the server PDF, both now on the FastAPI backend behind the proxy.
 * Markup, copy and the print fallback are unchanged.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { track } = vi.hoisted(() => ({ track: vi.fn() }));
vi.mock("@/lib/notice-pack/track", () => ({ track }));

import NoticePackClient from "@/app/[locale]/tools/dpdpa-privacy-notice-generator/NoticePackClient";

const CAPTURE_URL = "/api/proxy/api/v1/notices/capture";
const PDF_URL = "/api/proxy/api/v1/notices/pdf";

const READY_STATE = {
  org: "Sunrise Diagnostics",
  website: "sunrisediagnostics.in",
  sector: "clinic",
  data: ["Name", "Phone"],
  contexts: ["appointment"],
  purpose: { Name: "Identify you", Phone: "Service updates" },
  consentVia: ["Website"],
  withdrawMethod: ["Email"],
  withdrawContact: "privacy@sunrise.in",
  vendors: ["Cloud storage"],
  noVendors: false,
  children: "No",
  childWhy: [],
  retention: "3 years",
  cName: "Priya Nair",
  cEmail: "privacy@sunrise.in",
  cPhone: "",
  regAddress: "",
  slug: "",
  lang: "en",
};

function fetchMock() {
  return global.fetch as unknown as ReturnType<typeof vi.fn>;
}

function callTo(url: string) {
  return fetchMock().mock.calls.find((c) => c[0] === url) as [string, RequestInit] | undefined;
}

async function openResultView() {
  localStorage.setItem("np_state_v1", JSON.stringify({ S: READY_STATE, step: 7 }));
  const user = userEvent.setup();
  render(<NoticePackClient />);
  await user.click(await screen.findByRole("button", { name: /View my Notice Pack/ }));
  await screen.findByRole("heading", { name: "Your DPDPA Notice Pack is ready" });
  return user;
}

async function passTheGate(user: ReturnType<typeof userEvent.setup>) {
  // The input normalises its value on every change, which makes user.type's simulated
  // caret jump to the start in jsdom (text arrives reversed). Paste the whole value.
  await user.click(screen.getByPlaceholderText("Work email"));
  await user.paste("owner@sunrise.in");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: /Unlock my pack/ }));
}

describe("NoticePackClient", () => {
  beforeEach(() => {
    localStorage.clear();
    track.mockClear();
    global.fetch = vi.fn(async (url: string) =>
      url === PDF_URL
        ? { ok: true, status: 200, blob: async () => new Blob(["%PDF-1.4"], { type: "application/pdf" }) }
        : { ok: true, status: 200, json: async () => ({ success: true }) },
    ) as unknown as typeof fetch;
    URL.createObjectURL = vi.fn(() => "blob:notice");
    URL.revokeObjectURL = vi.fn();
  });

  it("renders the wizard with its unchanged headline", async () => {
    render(<NoticePackClient />);
    expect(
      await screen.findByRole("heading", { name: "Generate your DPDPA Notice Pack in minutes." }),
    ).toBeInTheDocument();
    expect(screen.getByText("About your business")).toBeInTheDocument();
  });

  it("gates the export behind the email form", async () => {
    const user = await openResultView();
    await user.click(screen.getByRole("button", { name: "Download PDF" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Get your Notice Pack" })).toBeInTheDocument();
    expect(callTo(PDF_URL)).toBeUndefined();
  });

  it("posts the captured lead to the backend through the proxy", async () => {
    const user = await openResultView();
    await user.click(screen.getByRole("button", { name: "Download PDF" }));
    await passTheGate(user);

    await waitFor(() => expect(callTo(CAPTURE_URL)).toBeDefined());
    const [, init] = callTo(CAPTURE_URL)!;
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.email).toBe("owner@sunrise.in");
    expect(body.business_name).toBe("Sunrise Diagnostics");
    expect(body.sector).toBe("Clinic / Diagnostic Lab");
    expect(body.export_type).toBe("pdf");
    expect(body.source).toBe("notice-generator");
    expect(body.consent).toBe(true);
    expect(body.hp_url).toBe("");
    expect(typeof body.readiness_score).toBe("number");
  });

  it("posts the whole notice state to the backend PDF route and downloads the blob", async () => {
    const user = await openResultView();
    await user.click(screen.getByRole("button", { name: "Download PDF" }));
    await passTheGate(user);

    await waitFor(() => expect(callTo(PDF_URL)).toBeDefined(), { timeout: 3000 });
    const [, init] = callTo(PDF_URL)!;
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.org).toBe("Sunrise Diagnostics");
    expect(body.sector).toBe("clinic");
    expect(body.lang).toBe("en");
    expect(typeof body.effIso).toBe("string");
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    expect(track).toHaveBeenCalledWith("notice_pdf_downloaded", { sector: "clinic" });
  });

  it("falls back to browser print when the backend PDF fails", async () => {
    const printFn = vi.fn();
    const openSpy = vi
      .spyOn(window, "open")
      .mockReturnValue({
        document: { write: vi.fn(), close: vi.fn() },
        focus: vi.fn(),
        print: printFn,
      } as unknown as Window);
    global.fetch = vi.fn(async (url: string) =>
      url === PDF_URL ? { ok: false, status: 500 } : { ok: true, status: 200, json: async () => ({}) },
    ) as unknown as typeof fetch;

    const user = await openResultView();
    await user.click(screen.getByRole("button", { name: "Download PDF" }));
    await passTheGate(user);

    await waitFor(() => expect(openSpy).toHaveBeenCalled(), { timeout: 3000 });
    await waitFor(() =>
      expect(track).toHaveBeenCalledWith("notice_pdf_downloaded", {
        sector: "clinic",
        fallback: "print",
      }),
    );
  });

  it("never blocks the export when the capture call fails", async () => {
    global.fetch = vi.fn(async (url: string) =>
      url === CAPTURE_URL
        ? Promise.reject(new Error("offline"))
        : { ok: true, status: 200, blob: async () => new Blob(["%PDF-1.4"]) },
    ) as unknown as typeof fetch;

    const user = await openResultView();
    await user.click(screen.getByRole("button", { name: "Download PDF" }));
    await passTheGate(user);

    expect(await screen.findByRole("heading", { name: "Unlocked." })).toBeInTheDocument();
  });
});
