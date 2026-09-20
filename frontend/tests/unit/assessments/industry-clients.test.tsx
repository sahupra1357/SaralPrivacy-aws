/**
 * The 12 sector scan clients. Each opens on its pack's first question and, on submit,
 * posts to the FastAPI backend through the core proxy. Inventory row A1.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NextIntlClientProvider } from "next-intl";
import type { ComponentType } from "react";
import messages from "@/messages/en.json";
import type { IndustryPack } from "@/lib/data/industry-assessment";

vi.mock("@/lib/analytics", () => ({
  trackEvent: new Proxy({}, { get: () => vi.fn() }),
}));

import CAAssessmentClient from "@/app/[locale]/assessment/ca-firms/CAAssessmentClient";
import ClinicAssessmentClient from "@/app/[locale]/assessment/clinics-diagnostic-labs/ClinicAssessmentClient";
import D2CAssessmentClient from "@/app/[locale]/assessment/d2c-brands/D2CAssessmentClient";
import FintechNbfcAssessmentClient from "@/app/[locale]/assessment/fintech-nbfc/FintechNbfcAssessmentClient";
import GymsSalonsSpasAssessmentClient from "@/app/[locale]/assessment/gyms-salons-spas/GymsSalonsSpasAssessmentClient";
import HotelsTravelAssessmentClient from "@/app/[locale]/assessment/hotels-travel/HotelsTravelAssessmentClient";
import LawFirmAssessmentClient from "@/app/[locale]/assessment/law-firms/LawFirmAssessmentClient";
import PharmaciesAssessmentClient from "@/app/[locale]/assessment/pharmacies/PharmaciesAssessmentClient";
import RealEstateAssessmentClient from "@/app/[locale]/assessment/real-estate/RealEstateAssessmentClient";
import RecruitmentAssessmentClient from "@/app/[locale]/assessment/recruitment/RecruitmentAssessmentClient";
import SchoolAssessmentClient from "@/app/[locale]/assessment/schools-colleges/SchoolAssessmentClient";
import TrainingAssessmentClient from "@/app/[locale]/assessment/training-institutes/TrainingAssessmentClient";

import { caFirmPack } from "@/lib/data/industry-assessment/packs/ca-firms";
import { clinicsDiagnosticLabsPack } from "@/lib/data/industry-assessment/packs/clinics-diagnostic-labs";
import { d2cBrandsPack } from "@/lib/data/industry-assessment/packs/d2c-brands";
import { fintechNbfcPack } from "@/lib/data/industry-assessment/packs/fintech-nbfc";
import { gymsSalonsSpasPack } from "@/lib/data/industry-assessment/packs/gyms-salons-spas";
import { hotelsTravelPack } from "@/lib/data/industry-assessment/packs/hotels-travel";
import { lawFirmsPack } from "@/lib/data/industry-assessment/packs/law-firms";
import { pharmaciesPack } from "@/lib/data/industry-assessment/packs/pharmacies";
import { realEstatePack } from "@/lib/data/industry-assessment/packs/real-estate";
import { recruitmentAgenciesPack } from "@/lib/data/industry-assessment/packs/recruitment-agencies";
import { schoolsCollegesPack } from "@/lib/data/industry-assessment/packs/schools-colleges";
import { trainingInstitutePack } from "@/lib/data/industry-assessment/packs/training-institutes";

const CLIENTS: Array<[string, ComponentType, IndustryPack]> = [
  ["ca-firms", CAAssessmentClient, caFirmPack],
  ["clinics-diagnostic-labs", ClinicAssessmentClient, clinicsDiagnosticLabsPack],
  ["d2c-brands", D2CAssessmentClient, d2cBrandsPack],
  ["fintech-nbfc", FintechNbfcAssessmentClient, fintechNbfcPack],
  ["gyms-salons-spas", GymsSalonsSpasAssessmentClient, gymsSalonsSpasPack],
  ["hotels-travel", HotelsTravelAssessmentClient, hotelsTravelPack],
  ["law-firms", LawFirmAssessmentClient, lawFirmsPack],
  ["pharmacies", PharmaciesAssessmentClient, pharmaciesPack],
  ["real-estate", RealEstateAssessmentClient, realEstatePack],
  ["recruitment", RecruitmentAssessmentClient, recruitmentAgenciesPack],
  ["schools-colleges", SchoolAssessmentClient, schoolsCollegesPack],
  ["training-institutes", TrainingAssessmentClient, trainingInstitutePack],
];

describe.each(CLIENTS)("%s scan", (_name, Client, pack) => {
  it("opens on the pack's first question", () => {
    render(
      <NextIntlClientProvider locale="en" messages={messages}>
        <Client />
      </NextIntlClientProvider>
    );

    expect(screen.getByText(pack.questions[0].question)).toBeInTheDocument();
    expect(
      screen.getByText(pack.questions[0].options[0].label)
    ).toBeInTheDocument();
  });
});

describe("pack registry", () => {
  it("gives every sector its own report_type, so the backend can pick the checklist", () => {
    const reportTypes = CLIENTS.map(([, , pack]) => pack.reportType);

    expect(new Set(reportTypes).size).toBe(CLIENTS.length);
  });
});
