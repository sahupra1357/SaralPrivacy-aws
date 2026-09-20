/**
 * app/(backoffice)/report/[token]/page.tsx — now reads the FastAPI backend instead of
 * `findOneBy("assessments", ...)`. Inventory row A2.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { notFound } from "next/navigation";

vi.mock("@/lib/api", () => ({ apiGet: vi.fn() }));
vi.mock("@/components/TemplateGateModal", () => ({
  default: ({ reportToken }: { reportToken: string }) => (
    <div data-testid="template-gate">{reportToken}</div>
  ),
}));

import { apiGet } from "@/lib/api";
import ReportPage from "@/app/(backoffice)/report/[token]/page";

const IN_90_DAYS = new Date(Date.now() + 90 * 864e5).toISOString();
const YESTERDAY = new Date(Date.now() - 864e5).toISOString();

const generalDoc = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "priya@example.com",
  name: "Priya",
  business_name: "Rangoli Retail",
  report_type: "quick",
  verdict_band: "Building Foundations",
  final_score: 59,
  report_token: "tok-general",
  report_token_expires_at: IN_90_DAYS,
  category_scores_json: JSON.stringify({
    noticeConsent: 70,
    accessControl: 40,
    retentionDeletion: 30,
    ownershipGovernance: 55,
    vendorPartnerRisk: 45,
    incidentReadiness: 20,
  }),
  answers_json: "{}",
  red_flags_json: JSON.stringify(["No consent record kept"]),
  immediate_actions_json: JSON.stringify(["Publish a privacy notice"]),
  thirty_day_actions_json: JSON.stringify(["Build a data inventory"]),
};

async function renderReport(token = "tok-general") {
  render(await ReportPage({ params: Promise.resolve({ token }) }));
}

describe("report page", () => {
  beforeEach(() => {
    vi.mocked(notFound).mockImplementation(() => {
      throw new Error("NEXT_NOT_FOUND");
    });
  });

  it("asks the backend for the token", async () => {
    vi.mocked(apiGet).mockResolvedValue(generalDoc);

    await renderReport("tok with space");

    expect(apiGet).toHaveBeenCalledWith("/assessments/report/tok%20with%20space", {
      revalidate: 0,
    });
  });

  it("renders the general readiness report", async () => {
    vi.mocked(apiGet).mockResolvedValue(generalDoc);

    await renderReport();

    expect(screen.getByText("Rangoli Retail's Readiness Report")).toBeInTheDocument();
    expect(screen.getByText("Prepared for Priya")).toBeInTheDocument();
    expect(screen.getByText("59")).toBeInTheDocument();
    expect(screen.getByText("Building Foundations")).toBeInTheDocument();
    expect(screen.getByText("No consent record kept")).toBeInTheDocument();
    expect(screen.getByText("Publish a privacy notice")).toBeInTheDocument();
    expect(screen.getByText("Build a data inventory")).toBeInTheDocument();
    expect(screen.getByTestId("template-gate")).toHaveTextContent("tok-general");
  });

  it("falls back to a generic heading when no business name was given", async () => {
    vi.mocked(apiGet).mockResolvedValue({ ...generalDoc, business_name: "", name: "" });

    await renderReport();

    expect(screen.getByText("Your DPDPA Readiness Report")).toBeInTheDocument();
    expect(screen.queryByText(/Prepared for/)).not.toBeInTheDocument();
  });

  it("renders the pack-aware industry report for an industry report_type", async () => {
    vi.mocked(apiGet).mockResolvedValue({
      ...generalDoc,
      report_type: "ca-firm",
      verdict_band: "High Risk",
      final_score: 44,
      category_scores_json: "{}",
    });

    await renderReport();

    expect(screen.getByText("Rangoli Retail's DPDPA Report")).toBeInTheDocument();
    expect(screen.getByText("Risk Band: High Risk")).toBeInTheDocument();
    expect(screen.getByText("Retake the Scan →")).toBeInTheDocument();
    // The industry report has no template gate or 30-day plan.
    expect(screen.queryByTestId("template-gate")).not.toBeInTheDocument();
  });

  it("shows the expiry screen once the token is past its date", async () => {
    vi.mocked(apiGet).mockResolvedValue({ ...generalDoc, report_token_expires_at: YESTERDAY });

    await renderReport();

    expect(screen.getByText("This report has expired")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Report links are valid for 90 days. Retake the assessment to get a fresh report."
      )
    ).toBeInTheDocument();
  });

  it("404s when the backend has no such report", async () => {
    vi.mocked(apiGet).mockRejectedValue(new Error("API 404"));

    await expect(renderReport("nope")).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("survives malformed JSON columns", async () => {
    vi.mocked(apiGet).mockResolvedValue({
      ...generalDoc,
      red_flags_json: "{not json",
      category_scores_json: "also not json",
    });

    await renderReport();

    expect(screen.getByText("Rangoli Retail's Readiness Report")).toBeInTheDocument();
    expect(screen.queryByText("Your Readiness by Area")).not.toBeInTheDocument();
  });
});
