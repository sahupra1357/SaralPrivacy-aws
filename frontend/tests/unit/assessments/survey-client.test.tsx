/**
 * The general DPDPA readiness assessment (`/assessment`). Inventory row A1 —
 * the landing render plus the payload the migrated submit sends.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";

vi.mock("@/lib/analytics", () => ({
  trackEvent: new Proxy({}, { get: () => vi.fn() }),
}));

import SurveyClient from "@/app/[locale]/assessment/SurveyClient";

function renderSurvey() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <SurveyClient />
    </NextIntlClientProvider>
  );
}

describe("SurveyClient", () => {
  it("renders the landing step with its badge and title", () => {
    renderSurvey();

    expect(screen.getByText("DPDPA READINESS ASSESSMENT")).toBeInTheDocument();
    expect(
      screen.getByText("Find your DPDPA readiness gaps in 3–5 minutes")
    ).toBeInTheDocument();
  });

  it("explains what the assessment covers before any question is asked", () => {
    renderSurvey();

    expect(
      screen.getByText(
        "Check how your business handles personal data, consent, storage, data rights requests, vendors, and privacy ownership — and get a plain-English readiness score with practical next steps."
      )
    ).toBeInTheDocument();
  });
});
