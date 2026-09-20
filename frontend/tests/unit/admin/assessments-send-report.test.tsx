/** /admin/assessments — list via the data route, "Send report" via /api/v1/admin/send-report. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AssessmentsPage from "@/app/(backoffice)/admin/assessments/page";
import { QUESTIONS } from "@/lib/data/dpdpa-assessment";

const q = QUESTIONS[0];
const assessment = {
  $id: "a1",
  id: "a1",
  $createdAt: "2026-09-01T10:00:00+00:00",
  email: "owner@clinic.test",
  name: "Meera",
  industry: "healthcare",
  final_score: 42,
  verdict_band: "Building Foundations",
  report_type: "quick",
  answers_json: JSON.stringify({ [q.key]: q.options[0].id }),
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("assessments page", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockImplementation(async (input) =>
      String(input).includes("/admin/data")
        ? json({ documents: [assessment], total: 1 })
        : json({ success: true }),
    );
  });

  it("loads assessments through the proxy", async () => {
    render(<AssessmentsPage />);
    expect(await screen.findAllByText("owner@clinic.test")).not.toHaveLength(0);
    expect(vi.mocked(global.fetch).mock.calls[0][0]).toBe(
      "/api/proxy/api/v1/admin/data?collection=assessments&limit=200",
    );
  });

  it("sends the report with the rebuilt answer summary", async () => {
    render(<AssessmentsPage />);
    await screen.findAllByText("owner@clinic.test");

    await userEvent.click(screen.getByRole("button", { name: "Resend" }));

    await waitFor(() =>
      expect(vi.mocked(global.fetch).mock.calls.some(([u]) => u === "/api/proxy/api/v1/admin/send-report")).toBe(true),
    );
    const call = vi.mocked(global.fetch).mock.calls.find(([u]) => u === "/api/proxy/api/v1/admin/send-report")!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({
      assessmentId: "a1",
      answerSummary: [{ question: q.text, answer: q.options[0].text }],
    });
    expect(await screen.findByText("Sent ✓")).toBeInTheDocument();
  });

  it("shows the backend error on the row when the send fails", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input) =>
      String(input).includes("/admin/data")
        ? json({ documents: [assessment], total: 1 })
        : json({ detail: "Assessment has no email address" }, 400),
    );
    render(<AssessmentsPage />);
    await screen.findAllByText("owner@clinic.test");
    await userEvent.click(screen.getByRole("button", { name: "Resend" }));
    expect(await screen.findByTitle("Assessment has no email address")).toHaveTextContent("Failed");
  });
});
