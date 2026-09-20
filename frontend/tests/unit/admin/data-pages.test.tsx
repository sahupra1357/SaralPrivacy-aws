/**
 * The client admin tables read the allowlisted backend reader through the proxy
 * (was /api/admin/data). Same query string, same `{documents}` envelope.
 */
import type { ReactElement } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LeadsPage from "@/app/(backoffice)/admin/leads/page";
import SubscribersPage from "@/app/(backoffice)/admin/subscribers/page";
import DownloadsPage from "@/app/(backoffice)/admin/downloads/page";
import DiscoveryLeadsPage from "@/app/(backoffice)/admin/discovery/page";
import ConsentLogPage from "@/app/(backoffice)/admin/consent/page";
import ConsultationsPage from "@/app/(backoffice)/admin/consultations/page";
import SurveyResponsesPage from "@/app/(backoffice)/admin/survey-responses/page";

const doc = {
  $id: "11111111-1111-1111-1111-111111111111",
  id: "11111111-1111-1111-1111-111111111111",
  $createdAt: "2026-09-01T10:00:00+00:00",
  created_at: "2026-09-01T10:00:00.000Z",
  name: "Leela Rao",
  email: "leela@example.com",
  company: "Acme",
  source: "consultation",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const CASES: Array<[string, () => ReactElement, string, boolean]> = [
  ["leads", () => <LeadsPage />, "/api/proxy/api/v1/admin/data?collection=leads&limit=200", true],
  ["subscribers", () => <SubscribersPage />, "/api/proxy/api/v1/admin/data?collection=subscribers&limit=200", true],
  ["downloads", () => <DownloadsPage />, "/api/proxy/api/v1/admin/data?collection=downloads&limit=200", true],
  [
    "discovery",
    () => <DiscoveryLeadsPage />,
    "/api/proxy/api/v1/admin/data?collection=template_downloads&source=discovery&limit=300",
    true,
  ],
  ["consent", () => <ConsentLogPage />, "/api/proxy/api/v1/admin/data?collection=consent_log&limit=500", true],
  [
    "consultations",
    () => <ConsultationsPage />,
    "/api/proxy/api/v1/admin/data?collection=leads&source=consultation&limit=200",
    true,
  ],
  [
    "survey-responses",
    () => <SurveyResponsesPage />,
    "/api/proxy/api/v1/admin/data?collection=survey_responses&limit=500",
    false,
  ],
];

describe("admin data tables", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockImplementation(async () => json({ documents: [doc], total: 1 }));
  });

  it.each(CASES)("%s reads the backend data route through the proxy", async (_name, page, url, showsEmail) => {
    render(page());

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(vi.mocked(global.fetch).mock.calls[0][0]).toBe(url);
    if (showsEmail) expect(await screen.findByText("leela@example.com")).toBeInTheDocument();
  });

  it("a failed fetch leaves the table empty instead of crashing", async () => {
    vi.mocked(global.fetch).mockRejectedValue(new Error("offline"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<LeadsPage />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByText("leela@example.com")).not.toBeInTheDocument();
  });
});
