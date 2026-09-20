import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The discovery engine, its data and the two presentational children are out of scope
// here: this test covers the lead form's data layer (proxy URL, payload, CSV download).
vi.mock("@/lib/discovery/engine", () => ({
  bandColor: () => "#E8704A",
  resolveGroups: () => [{ key: "g1", label: "Customers", items: [{ id: "i1", item: "Name" }] }],
  TAG_GROUP: { identity: "Identity" },
}));
vi.mock("@/lib/discovery/data", () => ({
  getNiche: () => ({ name: "Salons & Spas" }),
  TAG_LIB: { identity: { weight: 2 } },
}));
vi.mock("@/lib/discovery/register", () => ({
  buildRegister: () => ({
    counts: { items: 1, categories: 1, highRisk: 0 },
    rows: [],
    retention: [],
    risks: [],
  }),
}));
vi.mock("@/lib/discovery/checklist-map", () => ({
  checklistFor: () => ({
    dedicated: false,
    templateName: "Salon DPDPA Starter Checklist",
    pdfPath: "/templates/salon.pdf",
  }),
}));
vi.mock("@/lib/analytics", () => ({
  trackEvent: { discoveryComplete: vi.fn(), discoveryInventoryDownload: vi.fn() },
}));
vi.mock("@/app/[locale]/discovery/components/Gauge", () => ({
  default: () => <div data-testid="gauge" />,
}));
vi.mock("@/app/[locale]/discovery/components/pack/DiscoveryPack", () => ({
  default: () => <div data-testid="pack" />,
}));

import ResultPanel from "@/app/[locale]/discovery/components/ResultPanel";

const RESULT = {
  final: 62,
  riskBand: "High",
  confidence: "Medium",
  fixCount: 4,
  selectedCount: 1,
  totalCount: 10,
  hiddenSelected: [{ item: "CCTV footage" }],
  hiddenAvailable: 3,
  categories: ["Identity"],
  highValueCount: 2,
} as never;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderPanel() {
  return render(
    <ResultPanel
      nicheId="salons"
      result={RESULT}
      selected={new Set(["i1"])}
      onRestart={() => {}}
    />,
  );
}

async function openFormAndFill() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /download data inventory \(csv\)/i }));
  await user.type(screen.getByLabelText("Work email"), "meera@example.com");
  await user.type(screen.getByLabelText("Business name"), "Sunrise Salon");
  await user.type(screen.getByLabelText("Your name"), "Meera");
  await user.type(screen.getByLabelText("Phone"), "+919812345678");
  await user.selectOptions(screen.getByLabelText("Team size"), "1-10");
  return user;
}

describe("discovery ResultPanel lead form", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ success: true }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:x"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
  });

  it("posts the discovery lead through the backend proxy", async () => {
    renderPanel();

    const user = await openFormAndFill();
    await user.click(screen.getByRole("button", { name: /download my data inventory/i }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/forms/template-download");
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body).toMatchObject({
      businessName: "Sunrise Salon",
      employees: "1-10",
      contactName: "Meera",
      phone: "+919812345678",
      email: "meera@example.com",
      consentContact: true,
      consentBriefings: false,
      source: "discovery",
      reportToken: "salons",
      nicheName: "Salons & Spas",
      hp_url: "",
    });
    expect(body.inventoryCsv).toContain("Master Personal Data Register");
  });

  it("downloads the CSV and shows the sent state on success", async () => {
    renderPanel();

    const user = await openFormAndFill();
    await user.click(screen.getByRole("button", { name: /download my data inventory/i }));

    expect(await screen.findByText("Your data inventory is downloading.")).toBeInTheDocument();
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  it("rejects a malformed email before calling the backend", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: /download data inventory \(csv\)/i }));
    await user.type(screen.getByLabelText("Work email"), "not-an-email");
    await user.click(screen.getByRole("button", { name: /download my data inventory/i }));

    expect(await screen.findByText("Please enter a valid email address.")).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("shows the backend error message", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse({ detail: "Required fields missing." }, 400),
    );
    renderPanel();

    const user = await openFormAndFill();
    await user.click(screen.getByRole("button", { name: /download my data inventory/i }));

    expect(await screen.findByText("Required fields missing.")).toBeInTheDocument();
  });
});
